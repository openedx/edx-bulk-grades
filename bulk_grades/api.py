"""
Bulk Grading API.
"""


import logging
from collections import OrderedDict
from itertools import product

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.utils.functional import cached_property
from django.utils.translation import ugettext as _
from lms.djangoapps.grades import api as grades_api
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx.core.djangoapps.course_groups.cohorts import get_cohort
from super_csv.csv_processor import CSVProcessor, DeferrableMixin, ValidationError

from bulk_grades.clients import LearnerAPIClient

from .models import ScoreOverrider

__all__ = ('GradeCSVProcessor', 'ScoreCSVProcessor', 'get_score', 'get_scores', 'set_score')

log = logging.getLogger(__name__)

UNKNOWN_LAST_SCORE_OVERRIDER = 'unknown'


def _get_enrollments(course_id, track=None, cohort=None, active_only=False):
    """
    Return iterator of enrollment dictionaries.

    {
        'user': user object
        'user_id': user id
        'username': username
        'full_name': user's full name
        'enrolled': bool
        'track': enrollment mode
        'student_uid': institution user id from program enrollment
    }
    """
    enrollments = apps.get_model('student', 'CourseEnrollment').objects.filter(course_id=course_id).select_related(
        'user').prefetch_related('programcourseenrollment_set')
    if track:
        enrollments = enrollments.filter(mode=track)
    if cohort:
        enrollments = enrollments.filter(
            user__cohortmembership__course_id=course_id,
            user__cohortmembership__course_user_group__name=cohort)
    if active_only:
        enrollments = enrollments.filter(is_active=True)
    for enrollment in enrollments:
        enrollment_dict = {
            'user': enrollment.user,
            'user_id': enrollment.user.id,
            'username': enrollment.user.username,
            'full_name': enrollment.user.profile.name,
            'enrolled': enrollment.is_active,
            'track': enrollment.mode,
        }
        program_course_enrollment = enrollment.programcourseenrollment_set.all()
        if program_course_enrollment.exists():
            program_course_enrollment = program_course_enrollment.first().program_enrollment
            enrollment_dict['student_uid'] = program_course_enrollment.external_user_key
        else:
            enrollment_dict['student_uid'] = None
        yield enrollment_dict


class ScoreCSVProcessor(DeferrableMixin, CSVProcessor):
    """
    CSV Processor for file format defined for Staff Graded Points.
    """

    columns = ['user_id', 'username', 'full_name', 'student_uid',
               'enrolled', 'track', 'cohort', 'block_id', 'title', 'date_last_graded',
               'who_last_graded', 'Previous Points', 'New Points']
    required_columns = ['user_id', 'New Points', 'block_id', 'Previous Points']

    # files larger than 100 rows will be processed asynchronously
    size_to_defer = 100
    max_file_size = 4 * 1024 * 1024
    handle_undo = False

    def __init__(self, **kwargs):
        """
        Create a ScoreCSVProcessor.
        """
        self.max_points = 1
        self.user_id = None
        self.track = None
        self.cohort = None
        self.display_name = ''
        super().__init__(**kwargs)
        self._users_seen = set()

    def get_unique_path(self):
        """
        Return a unique id for CSVOperations.
        """
        return self.block_id

    def validate_row(self, row):
        """
        Validate CSV row.
        """
        super().validate_row(row)
        if row['block_id'] != self.block_id:
            raise ValidationError(_('The CSV does not match this problem. Check that you uploaded the right CSV.'))
        if row['New Points']:
            try:
                points = float(row['New Points'])
            except ValueError as error:
                raise ValidationError(_('Points must be numbers.')) from error
            if points > self.max_points:
                raise ValidationError(_('Points must not be greater than {}.').format(self.max_points))
            if points < 0:
                raise ValidationError(_('Points must be greater than 0'))

    # pylint: disable=inconsistent-return-statements
    def preprocess_row(self, row):
        """
        Preprocess CSV row.
        """
        if row['New Points'] and row['user_id'] not in self._users_seen:
            to_save = {
                'user_id': row['user_id'],
                'block_id': self.block_id,
                'new_points': float(row['New Points']),
                'max_points': self.max_points,
                'override_user_id': self.user_id,
            }
            self._users_seen.add(row['user_id'])
            return to_save

    def process_row(self, row):
        """
        Set the score for the given row, returning (status, undo).

        undo is a dict of an operation which would undo the set_score. In this case,
        that means we would have to call get_score, which could be expensive to do for the entire file.
        """
        if self.handle_undo:
            # get the current score, for undo. expensive
            undo = get_score(row['block_id'], row['user_id'])
            undo['new_points'] = undo['score']
            undo['max_points'] = row['max_points']
        else:
            undo = None
        set_score(row['block_id'], row['user_id'], row['new_points'], row['max_points'], row['override_user_id'])
        return True, undo

    def get_rows_to_export(self):
        """
        Return iterator of rows for file export.
        """
        location = UsageKey.from_string(self.block_id)
        my_name = self.display_name

        students = get_scores(location)
        course_key = location.course_key
        enrollments = _get_enrollments(course_key,
                                       track=self.track,
                                       cohort=self.cohort)
        for enrollment in enrollments:
            cohort = get_cohort(enrollment['user'], course_key, assign=False)
            row = {
                'block_id': location,
                'title': my_name,
                'New Points': None,
                'Previous Points': None,
                'date_last_graded': None,
                'who_last_graded': None,
                'user_id': enrollment['user_id'],
                'username': enrollment['username'],
                'full_name': enrollment['full_name'],
                'student_uid': enrollment['student_uid'],
                'enrolled': enrollment['enrolled'],
                'track': enrollment['track'],
                'cohort': cohort.name if cohort else None,
            }
            score = students.get(enrollment['user_id'], None)

            if score:
                row['Previous Points'] = float(score['score'])
                row['date_last_graded'] = score['modified'].strftime('%Y-%m-%d %H:%M')
                row['who_last_graded'] = score['who_last_graded']
            yield row

    def commit(self, running_task=None):
        """
        Commit the data and trigger course grade recalculation.
        """
        super().commit(running_task=running_task)
        if running_task or not self.status()['waiting']:
            # after commit, trigger grade recomputation for the course.
            # not sure if this is necessary
            course_key = UsageKey.from_string(self.block_id).course_key
            grades_api.task_compute_all_grades_for_course.apply_async(kwargs={'course_key': str(course_key)})


class GradedSubsectionMixin:
    """
    Mixin to help generated lists of graded subsections
    and appropriate column names for each.
    """

    def append_columns(self, new_column_names):
        """
        Appends items from ``new_column_names`` to ``self.columns``
        if the item is not already contained therein.
        """
        current_columns = set(self.columns)
        for new_column_name in new_column_names:
            if new_column_name not in current_columns:
                self.columns.append(new_column_name)

    @staticmethod
    def _get_graded_subsections(course_id, filter_subsection=None, filter_assignment_type=None):
        """
        Return list of graded subsections.

        If filter_subsection (block usage id) is set, return only that subsection.
        If filter_assignment_type (string) is set, return only subsections of the appropriate type.
        """
        subsections = OrderedDict()
        for subsection in grades_api.graded_subsections_for_course_id(course_id):
            block_id = str(subsection.location.block_id)
            if (  # pragma: no branch
                    (filter_subsection and (block_id != filter_subsection.block_id))
                    or
                    (filter_assignment_type and (filter_assignment_type != str(subsection.format)))
            ):
                continue  # pragma: no cover
            short_block_id = block_id[:8]
            if short_block_id not in subsections:
                subsections[short_block_id] = (subsection, subsection.display_name)
        return subsections

    @staticmethod
    def _subsection_column_names(short_subsection_ids, prefixes):
        """
        Given an iterable of ``short_subsection_ids`` (usually from ``_get_graded_subsections`` above),
        and ``prefixes`` to append to each, returns a list of names
        formed from the product of the subsection ids and prefixes.
        """
        return [f'{prefix}-{short_id}' for short_id, prefix in product(short_subsection_ids, prefixes)]


def decode_utf8(input_iterator):
    """
    Generator that decodes a utf-8 encoded
    input line by line
    """
    for line in input_iterator:
        yield line if isinstance(line, str) else line.decode('utf-8')


class GradeCSVProcessor(DeferrableMixin, GradedSubsectionMixin, CSVProcessor):
    """
    CSV Processor for subsection grades.
    """

    required_columns = ['user_id', 'course_id']
    subsection_prefixes = ('name', 'grade', 'original_grade', 'previous_override', 'new_override',)

    def __init__(self, **kwargs):
        """
        Create GradeCSVProcessor.
        """
        # First, set some default values.
        self.columns = ['user_id', 'username', 'student_key', 'course_id', 'track', 'cohort']
        self.course_id = None
        self.subsection_grade_max = None
        self.subsection_grade_min = None
        self.course_grade_min = None
        self.course_grade_max = None
        self.subsection = None
        self.track = None
        self.cohort = None
        self.user_id = None
        self.active_only = False

        # The CSVProcessor.__init__ method will set attributes on self
        # from items in kwargs, so this super().__init__() call can
        # override any attribute values assigned above.
        super().__init__(**kwargs)

        self._course_key = CourseKey.from_string(self.course_id)
        self._subsection = UsageKey.from_string(self.subsection) if self.subsection else None
        self._subsections = self._get_graded_subsections(
            self._course_key,
            filter_subsection=self._subsection,
            filter_assignment_type=kwargs.get('assignment_type', None),
        )
        self.append_columns(
            self._subsection_column_names(
                self._subsections.keys(),  # pylint: disable=dict-keys-not-iterating, useless-suppression
                self.subsection_prefixes
            )
        )
        self._users_seen = set()

    # pylint: disable=inconsistent-return-statements
    @cached_property
    def _user(self):
        if self.user_id:
            return get_user_model().objects.get(id=self.user_id)

    def save(self, operation_name=None, operating_user=None):
        """
        Saves the operation state for this processor, including the user
        who is performing the operation.
        """
        return super().save(operating_user=self._user)

    def get_unique_path(self):
        """
        Return a unique id for CSVOperations.
        """
        return self.course_id

    def validate_row(self, row):
        """
        Validate row.
        """
        super().validate_row(row)
        if row['course_id'] != self.course_id:
            raise ValidationError(_('Wrong course id {} != {}').format(row['course_id'], self.course_id))

    def preprocess_file(self, reader):
        """
        Preprocess the file, saving original data no matter whether there are errors.
        """
        super().preprocess_file(reader)
        self.save()

    def preprocess_row(self, row):
        """
        Preprocess the CSV row.
        """
        operation = {}
        if row['user_id'] in self._users_seen:
            return operation
        for key in row:
            if key.startswith('new_override-'):
                value = row[key].strip()
                if value:
                    short_id = key.split('-', 1)[1]
                    subsection = self._subsections[short_id][0]
                    operation['user_id'] = row['user_id']
                    operation['course_id'] = self.course_id
                    operation['block_id'] = str(subsection.location)
                    try:
                        operation['new_override'] = float(value)
                    except ValueError as error:
                        raise ValidationError(_('Grade must be a number')) from error
                    if operation['new_override'] < 0:
                        raise ValidationError(_('Grade must be positive'))
        self._users_seen.add(row['user_id'])
        return operation

    def process_row(self, row):
        """
        Save a row to the persistent subsection override table.
        """
        grades_api.override_subsection_grade(
            row['user_id'],
            row['course_id'],
            row['block_id'],
            overrider=self._user,
            earned_graded=row['new_override'],
            feature='grade-import',
            comment='Bulk Grade Import',
        )
        return True, None

    def get_rows_to_export(self):
        """
        Return iterator of rows to export.
        """
        enrollments = list(_get_enrollments(self._course_key, track=self.track, cohort=self.cohort, active_only=self.active_only))
        enrolled_users = [enroll['user'] for enroll in enrollments]

        grades_api.prefetch_course_and_subsection_grades(self._course_key, enrolled_users)

        for enrollment in enrollments:
            cohort = get_cohort(enrollment['user'], self._course_key, assign=False)
            row = {
                'user_id': enrollment['user_id'],
                'username': enrollment['username'],
                'student_key': enrollment['student_uid'] if enrollment['track'] == 'masters' else None,
                'track': enrollment['track'],
                'course_id': self.course_id,
                'cohort': cohort.name if cohort else None,
            }
            grades = grades_api.get_subsection_grades(enrollment['user_id'], self._course_key)
            if self._subsection and (self.subsection_grade_max or self.subsection_grade_min):
                short_id = self._subsection.block_id[:8]
                (filtered_subsection, _) = self._subsections[short_id]
                subsection_grade = grades.get(filtered_subsection.location, None)
                if not subsection_grade:
                    continue
                try:
                    effective_grade = (subsection_grade.override.earned_graded_override
                                       / subsection_grade.override.possible_graded_override) * 100
                except AttributeError:
                    effective_grade = (subsection_grade.earned_graded / subsection_grade.possible_graded) * 100
                if (  # pragma: no brach
                        (self.subsection_grade_min and (effective_grade < self.subsection_grade_min))
                        or
                        (self.subsection_grade_max and (effective_grade > self.subsection_grade_max))
                ):
                    continue
            # pylint: disable=E1111
            course_grade = grades_api.CourseGradeFactory().read(enrollment['user'], course_key=self._course_key)
            course_grade_normalized = course_grade.percent * 100

            if ((self.course_grade_min and course_grade_normalized < self.course_grade_min) or
                    (self.course_grade_max and course_grade_normalized > self.course_grade_max)):
                continue

            for block_id, (subsection, display_name) in self._subsections.items():
                row[f'name-{block_id}'] = display_name
                grade = grades.get(subsection.location, None)
                if grade:
                    effective_grade = grade.earned_graded
                    row[f'original_grade-{block_id}'] = grade.earned_graded
                    try:
                        effective_grade = grade.override.earned_graded_override
                        row[f'previous_override-{block_id}'] = grade.override.earned_graded_override
                    except AttributeError:
                        row[f'previous_override-{block_id}'] = None
                    row[f'grade-{block_id}'] = effective_grade
            yield row


class InterventionCSVProcessor(GradedSubsectionMixin, CSVProcessor):
    """
    CSV Processor for intervention report grades for masters track only.
    """

    MASTERS_TRACK = 'masters'
    subsection_prefixes = ('name', 'grade',)

    def __init__(self, **kwargs):
        """
        Create InterventionCSVProcessor.
        """
        # Set some default values for the attributes below
        self.columns = [
            'user_id', 'username', 'email', 'student_key', 'full_name', 'course_id', 'track', 'cohort',
            'number of videos overall', 'number of videos last week', 'number of problems overall',
            'number of problems last week',
            'number of correct problems overall', 'number of correct problems last week',
            'number of problem attempts overall', 'number of problem attempts last week',
            'number of forum posts overall', 'number of forum posts last week',
            'date last active',
        ]
        self.course_id = None
        self.cohort = None
        self.subsection = None
        self.assignment_type = None
        self.subsection_grade_min = None
        self.subsection_grade_max = None
        self.course_grade_min = None
        self.course_grade_max = None

        # The CSVProcessor.__init__ method will set attributes on self
        # from items in kwargs, so this super().__init__() call will
        # potentially override any attribute values assigned above.
        super().__init__(**kwargs)

        self._course_key = CourseKey.from_string(self.course_id)
        self._subsection = UsageKey.from_string(self.subsection) if self.subsection else None
        self._subsections = self._get_graded_subsections(
            self._course_key,
            filter_subsection=self._subsection,
            filter_assignment_type=self.assignment_type,
        )
        self.append_columns(
            self._subsection_column_names(
                self._subsections.keys(),  # pylint: disable=dict-keys-not-iterating, useless-suppression
                self.subsection_prefixes
            )
        )
        self.append_columns(('course grade letter', 'course grade numeric'))

    def get_rows_to_export(self):
        """
        Return iterator of rows to export.
        """
        enrollments = list(_get_enrollments(self._course_key, track=self.MASTERS_TRACK, cohort=self.cohort))
        grades_api.prefetch_course_and_subsection_grades(self._course_key, [enroll['user'] for enroll in enrollments])
        client = LearnerAPIClient()
        intervention_list = client.courses(self.course_id).user_engagement().get()
        intervention_data = {val['username']: val for val in intervention_list}
        for enrollment in enrollments:
            grades = grades_api.get_subsection_grades(enrollment['user_id'], self._course_key)
            if self._subsection and (self.subsection_grade_max or self.subsection_grade_min):
                short_id = self._subsection.block_id[:8]
                (filtered_subsection, _) = self._subsections[short_id]
                subsection_grade = grades.get(filtered_subsection.location, None)
                if not subsection_grade:
                    continue
                try:
                    effective_grade = (subsection_grade.override.earned_graded_override
                                       / subsection_grade.override.possible_graded_override) * 100
                except AttributeError:
                    effective_grade = (subsection_grade.earned_graded / subsection_grade.possible_graded) * 100
                if (
                        (self.subsection_grade_min and (effective_grade < self.subsection_grade_min))
                        or
                        (self.subsection_grade_max and (effective_grade > self.subsection_grade_max))
                ):
                    continue
            # pylint: disable=E1111
            course_grade = grades_api.CourseGradeFactory().read(enrollment['user'], course_key=self._course_key)
            if self.course_grade_min or self.course_grade_max:
                course_grade_normalized = (course_grade.percent * 100)

                if (
                        (self.course_grade_min and (course_grade_normalized < self.course_grade_min))
                        or
                        (self.course_grade_max and (course_grade_normalized > self.course_grade_max))
                ):
                    continue

            cohort = get_cohort(enrollment['user'], self._course_key, assign=False)
            int_user = intervention_data.get(enrollment['user'].username, {})
            row = {
                'user_id': enrollment['user_id'],
                'username': enrollment['username'],
                'email': enrollment['user'].email,
                'student_key': enrollment['student_uid'],
                'full_name': enrollment['full_name'],
                'track': enrollment['track'],
                'course_id': self.course_id,
                'cohort': cohort.name if cohort else None,
                'number of videos overall': int_user.get('videos_overall', 0),
                'number of videos last week': int_user.get('videos_last_week', 0),
                'number of problems overall': int_user.get('problems_overall', 0),
                'number of problems last week': int_user.get('problems_last_week', 0),
                'number of correct problems overall': int_user.get('correct_problems_overall', 0),
                'number of correct problems last week': int_user.get('correct_problems_last_week', 0),
                'number of problem attempts overall': int_user.get('problems_attempts_overall', 0),
                'number of problem attempts last week': int_user.get('problems_attempts_last_week', 0),
                'number of forum posts overall': int_user.get('forum_posts_overall', 0),
                'number of forum posts last week': int_user.get('forum_posts_last_week', 0),
                'date last active': int_user.get('date_last_active', 0),
                'course grade letter': course_grade.letter_grade,
                'course grade numeric': course_grade.percent
            }
            for block_id, (subsection, display_name) in self._subsections.items():
                row[f'name-{block_id}'] = display_name
                grade = grades.get(subsection.location, None)
                if grade:
                    if getattr(grade, 'override', None):
                        row[f'grade-{block_id}'] = grade.override.earned_graded_override
                    else:
                        row[f'grade-{block_id}'] = grade.earned_graded
            yield row


def set_score(usage_key, student_id, score, max_points, override_user_id=None, **defaults):
    """
    Set a score.
    """
    if not isinstance(usage_key, UsageKey):
        usage_key = UsageKey.from_string(usage_key)
    defaults['module_type'] = 'problem'
    if score < 0:
        raise ValueError(_('score must be positive'))
    defaults['grade'] = score
    defaults['max_grade'] = max_points
    module = apps.get_model('courseware', 'StudentModule').objects.update_or_create(
        student_id=student_id,
        course_id=usage_key.course_key,
        module_state_key=usage_key,
        defaults=defaults)[0]
    if override_user_id:
        ScoreOverrider.objects.create(
            module=module,
            user_id=override_user_id)


def get_score(usage_key, user_id):
    """
    Return score for user_id and usage_key.
    """
    try:
        return get_scores(usage_key, [user_id])[int(user_id)]
    except KeyError:
        return None


def get_scores(usage_key, user_ids=None):
    """
    Return dictionary of student_id: scores.
    """
    if not isinstance(usage_key, UsageKey):
        usage_key = UsageKey.from_string(usage_key)
    scores_qset = apps.get_model('courseware', 'StudentModule').objects.filter(
        course_id=usage_key.course_key,
        module_state_key=usage_key,
    )
    if user_ids:
        scores_qset = scores_qset.filter(student_id__in=user_ids)

    scores = {}
    for row in scores_qset:
        scores[row.student_id] = {
            'score': row.grade,
            'max_grade': row.max_grade,
            'created': row.created,
            'modified': row.modified,
            'state': row.state,
        }
        try:
            last_override = row.scoreoverrider_set.select_related('user').latest('created')
        except ObjectDoesNotExist:
            scores[row.student_id]['who_last_graded'] = UNKNOWN_LAST_SCORE_OVERRIDER
        else:
            scores[row.student_id]['who_last_graded'] = last_override.user.username
    return scores
