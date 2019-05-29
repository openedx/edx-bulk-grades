from __future__ import absolute_import, unicode_literals
import logging

from six import iteritems, text_type
from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist

from openedx.core.djangoapps.course_groups.cohorts import get_cohort
from lms.djangoapps.grades import api as grades_api

from super_csv.csv_processor import ChecksumMixin, CSVProcessor, DeferrableMixin

from django.utils.translation import ugettext as _
from opaque_keys.edx.keys import UsageKey, CourseKey

from .models import ScoreOverrider


__all__ = ('ScoreCSVProcessor', 'get_score', 'get_scores', 'set_score')

log = logging.getLogger(__name__)


def _get_enrollments(course_id, track=None, cohort=None):
    enrollments = apps.get_model('student', 'CourseEnrollment').objects.filter(
        course_id=course_id).select_related('user', 'programcourseenrollment')

    if track:
        enrollments = enrollments.filter(mode=track)
    if cohort:
        enrollments = enrollments.filter(
            user__cohortmembership__course_id=course_id,
            user__cohortmembership__course_user_group__name=cohort)
    for enrollment in enrollments:
        enrollment_dict = {
            'user': enrollment.user,
            'user_id': enrollment.user.id,
            'username': enrollment.user.username,
            'full_name': enrollment.user.profile.name,
            'enrolled': enrollment.is_active,
            'track': enrollment.mode,
        }
        try:
            pce = enrollment.programcourseenrollment.program_enrollment
            enrollment_dict['student_uid'] = pce.external_user_key
        except ObjectDoesNotExist:
            enrollment_dict['student_uid'] = None
        yield enrollment_dict


class ScoreCSVProcessor(ChecksumMixin, DeferrableMixin, CSVProcessor):
    """
    CSV Processor for file format defined for Staff Graded Points
    """
    columns = ['user_id', 'username', 'full_name', 'student_uid',
               'enrolled', 'track', 'block_id', 'title', 'date_last_graded',
               'who_last_graded', 'csum', 'last_points', 'points']
    required_columns = ['user_id', 'points', 'csum', 'block_id', 'last_points']
    checksum_columns = ['user_id', 'block_id', 'last_points']
    # files larger than 100 rows will be processed asynchronously
    size_to_defer = 100
    max_file_size = 4 * 1024 * 1024
    handle_undo = False

    def __init__(self, **kwargs):
        self.max_points = 1
        self.user_id = None
        self.track = None
        self.cohort = None
        self.display_name = ''
        super(ScoreCSVProcessor, self).__init__(**kwargs)
        self.users_seen = {}

    def get_unique_path(self):
        return self.block_id

    def validate_row(self, row):
        if not super(ScoreCSVProcessor, self).validate_row(row):
            return False
        if row['block_id'] != self.block_id:
            self.add_error(_('The CSV does not match this problem. Check that you uploaded the right CSV.'))
            return False
        if row['points']:
            try:
                if float(row['points']) > self.max_points:
                    self.add_error(_('Points must not be greater than {}.').format(self.max_points))
                    return False
            except ValueError:
                self.add_error(_('Points must be numbers.'))
                return False
        return True

    def preprocess_row(self, row):
        if row['points'] and row['user_id'] not in self.users_seen:
            to_save = {
                'user_id': row['user_id'],
                'block_id': self.block_id,
                'new_points': float(row['points']),
                'max_points': self.max_points,
                'override_user_id': self.user_id,
            }
            self.users_seen[row['user_id']] = 1
            return to_save

    def process_row(self, row):
        """
        Set the score for the given row, returning (status, undo)
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

        enrollments = _get_enrollments(location.course_key,
                                       track=self.track,
                                       cohort=self.cohort)
        for enrollment in enrollments:
            row = {
                'block_id': location,
                'title': my_name,
                'points': None,
                'last_points': None,
                'date_last_graded': None,
                'who_last_graded': None,
            }
            row.update(enrollment)
            score = students.get(enrollment['user_id'], None)

            if score:
                row['last_points'] = int(score['grade'] * self.max_points)
                row['date_last_graded'] = score['modified']
                row['who_last_graded'] = score['who_last_graded']
            yield row


class GradeCSVProcessor(DeferrableMixin, CSVProcessor):
    columns = ['user_id', 'username', 'course_id', 'track', 'cohort']
    required_columns = ['user_id', 'course_id']

    def __init__(self, **kwargs):
        self.course_id = None
        self.track = self.cohort = None
        super(GradeCSVProcessor, self).__init__(**kwargs)
        self.course_key = CourseKey.from_string(self.course_id)
        self.subsections = self._get_graded_subsections(self.course_key)

    def _get_graded_subsections(self, course_id):
        course_data = grades_api.course_data.CourseData(user=None, course_key=course_id)
        subsections = {}
        for subsection in grades_api.context.graded_subsections_for_course(course_data.collected_structure):
            short_block_id = subsection.location.block_id[:8]
            if short_block_id not in subsections:
                for key in ('name', 'grade', 'previous', 'new_grade'):
                    self.columns.append('{}-{}'.format(key, short_block_id))
                subsections[short_block_id] = (subsection, subsection.display_name)
        return subsections

    def validate_row(self, row):
        if not super(GradeCSVProcessor, self).validate_row(row):
            return False
        if row['course_id'] != self.course_id:
            self.add_error(_('Wrong course id %s != %s', row['course_id'], self.course_id))
            return False
        return True

    def preprocess_row(self, row):
        operation = {}
        for key in row:
            if key.startswith('new_grade-'):
                value = row[key].strip()
                if value:
                    short_id = key.split('-', 1)[1]
                    subsection, display_name = self.subsections[short_id]
                    operation['user_id'] = row['user_id']
                    operation['course_id'] = self.course_id
                    operation['block_id'] = text_type(subsection.location)
                    operation['new_grade'] = value
        return operation

    def process_row(self, row):
        raise NotImplementedError(self.process_row)

    def get_rows_to_export(self):
        enrollments = list(_get_enrollments(self.course_key, track=self.track, cohort=self.cohort))
        grades_api.prefetch_course_and_subsection_grades(self.course_key, [enroll['user'] for enroll in enrollments])
        for enrollment in enrollments:
            cohort = get_cohort(enrollment['user'], self.course_key, assign=False)
            row = {
                'user_id': enrollment['user_id'],
                'username': enrollment['username'],
                'track': enrollment['track'],
                'course_id': self.course_id,
                'cohort': cohort.name if cohort else None,
            }
            course_data = grades_api.course_data.CourseData(user=enrollment['user'], course_key=self.course_key)
            factory = grades_api.SubsectionGradeFactory(enrollment['user'], course_data=course_data)
            for block_id, (subsection, display_name) in iteritems(self.subsections):
                grade = factory.create(subsection, read_only=True)
                log.info(repr(grade.__dict__))
                row['name-{}'.format(block_id)] = display_name
                row['grade-{}'.format(block_id)] = grade.graded_total.earned
                row['previous-{}'.format(block_id)] = grade.override.earned_all_override if grade.override else None
            yield row



def set_score(usage_key, student_id, score, max_points, override_user_id=None, **defaults):
    """
    Set a score.
    """
    if not isinstance(usage_key, UsageKey):
        usage_key = UsageKey.from_string(usage_key)
    defaults['module_type'] = 'problem'
    defaults['grade'] = score / (max_points or 1.0)
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
            'grade': row.grade,
            'score': row.grade * (row.max_grade or 1),
            'max_grade': row.max_grade,
            'created': row.created,
            'modified': row.modified,
            'state': row.state,
        }
        last_override = row.scoreoverrider_set.latest('created')
        if last_override:
            scores[row.student_id]['who_last_graded'] = last_override.user_id
    return scores
