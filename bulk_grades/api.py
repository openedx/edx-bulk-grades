"""
Bulk Grading API.
"""
from __future__ import absolute_import, division, unicode_literals

import logging

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import ugettext as _
from opaque_keys.edx.keys import CourseKey, UsageKey
from six import iteritems, text_type
from super_csv.csv_processor import ChecksumMixin, CSVProcessor, DeferrableMixin, ValidationError

from lms.djangoapps.grades import api as grades_api
from openedx.core.djangoapps.course_groups.cohorts import get_cohort

from .models import ScoreOverrider

__all__ = ('GradeCSVProcessor', 'ScoreCSVProcessor', 'get_score', 'get_scores', 'set_score')

log = logging.getLogger(__name__)


def _get_enrollments(course_id, track=None, cohort=None):
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
    CSV Processor for file format defined for Staff Graded Points.
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
        """
        Create a ScoreCSVProcessor.
        """
        self.max_points = 1
        self.user_id = None
        self.track = None
        self.cohort = None
        self.display_name = ''
        super(ScoreCSVProcessor, self).__init__(**kwargs)
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
        super(ScoreCSVProcessor, self).validate_row(row)
        if row['block_id'] != self.block_id:
            raise ValidationError(_('The CSV does not match this problem. Check that you uploaded the right CSV.'))
        if row['points']:
            try:
                points = float(row['points'])
            except ValueError:
                raise ValidationError(_('Points must be numbers.'))
            if points > self.max_points:
                raise ValidationError(_('Points must not be greater than {}.').format(self.max_points))

    def preprocess_row(self, row):
        """
        Preprocess CSV row.
        """
        if row['points'] and row['user_id'] not in self._users_seen:
            to_save = {
                'user_id': row['user_id'],
                'block_id': self.block_id,
                'new_points': float(row['points']),
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
                'user_id': enrollment['user_id'],
                'username': enrollment['username'],
                'full_name': enrollment['full_name'],
                'student_uid': enrollment['student_uid'],
                'enrolled': enrollment['enrolled'],
                'track': enrollment['track'],
            }
            score = students.get(enrollment['user_id'], None)

            if score:
                row['last_points'] = float(score['grade'] * self.max_points)
                row['date_last_graded'] = score['modified']
                row['who_last_graded'] = score['who_last_graded']
            yield row


class GradeCSVProcessor(DeferrableMixin, CSVProcessor):
    """
    CSV Processor for subsection grades.
    """

    required_columns = ['user_id', 'course_id']

    def __init__(self, **kwargs):
        """
        Create GradeCSVProcessor.
        """
        self.columns = ['user_id', 'username', 'course_id', 'track', 'cohort']
        self.course_id = None
        self.track = self.cohort = self._user = None
        super(GradeCSVProcessor, self).__init__(**kwargs)
        self._course_key = CourseKey.from_string(self.course_id)
        self._subsections = self._get_graded_subsections(self._course_key)
        self._users_seen = set()

    def get_unique_path(self):
        """
        Return a unique id for CSVOperations.
        """
        return self.course_id

    def _get_graded_subsections(self, course_id):
        """
        Return list of graded subsections.
        """
        subsections = {}
        for subsection in grades_api.graded_subsections_for_course_id(course_id):
            short_block_id = subsection.location.block_id[:8]
            if short_block_id not in subsections:
                for key in ('name', 'grade', 'previous', 'new_grade'):
                    self.columns.append('{}-{}'.format(key, short_block_id))
                subsections[short_block_id] = (subsection, subsection.display_name)
        return subsections

    def validate_row(self, row):
        """
        Validate row.
        """
        super(GradeCSVProcessor, self).validate_row(row)
        if row['course_id'] != self.course_id:
            raise ValidationError(_('Wrong course id {} != {}').format(row['course_id'], self.course_id))

    def preprocess_row(self, row):
        """
        Preprocess the CSV row.
        """
        operation = {}
        if row['user_id'] in self._users_seen:
            return operation
        for key in row:
            if key.startswith('new_grade-'):
                value = row[key].strip()
                if value:
                    short_id = key.split('-', 1)[1]
                    subsection = self._subsections[short_id][0]
                    operation['user_id'] = row['user_id']
                    operation['course_id'] = self.course_id
                    operation['block_id'] = text_type(subsection.location)
                    try:
                        operation['new_grade'] = float(value)
                    except ValueError:
                        raise ValidationError(_('Grade must be a number'))
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
                earned_all=row['new_grade'],
                feature='grade-import'
        )
        return True, None

    def get_rows_to_export(self):
        """
        Return iterator of rows to export.
        """
        enrollments = list(_get_enrollments(self._course_key, track=self.track, cohort=self.cohort))
        grades_api.prefetch_course_and_subsection_grades(self._course_key, [enroll['user'] for enroll in enrollments])
        for enrollment in enrollments:
            cohort = get_cohort(enrollment['user'], self._course_key, assign=False)
            row = {
                'user_id': enrollment['user_id'],
                'username': enrollment['username'],
                'track': enrollment['track'],
                'course_id': self.course_id,
                'cohort': cohort.name if cohort else None,
            }
            grades = grades_api.get_subsection_grades(enrollment['user_id'], self._course_key)
            for block_id, (subsection, display_name) in iteritems(self._subsections):
                row['name-{}'.format(block_id)] = display_name
                grade = grades.get(subsection.location, None)
                if grade:
                    row['grade-{}'.format(block_id)] = grade.earned_all
                    row['previous-{}'.format(block_id)] = (
                        grade.override.earned_graded_override if grade.override else None)
            yield row


def set_score(usage_key, student_id, score, max_points, override_user_id=None, **defaults):
    """
    Set a score.
    """
    if not isinstance(usage_key, UsageKey):
        usage_key = UsageKey.from_string(usage_key)
    defaults['module_type'] = 'problem'
    defaults['grade'] = score / float(max_points or 1.0)
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
        try:
            last_override = row.scoreoverrider_set.latest('created')
        except ObjectDoesNotExist:
            pass
        else:
            scores[row.student_id]['who_last_graded'] = last_override.user_id
    return scores
