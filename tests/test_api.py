#!/usr/bin/env python
"""
Tests for the `edx-bulk-grades` api module.
"""


import datetime
from copy import deepcopy
from itertools import chain, cycle, product, repeat
from unittest.mock import MagicMock, Mock, patch

import ddt
import lms.djangoapps.grades.api as grades_api
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase
from opaque_keys.edx.keys import UsageKey
from student.models import CourseAccessRole, CourseEnrollment, Profile, ProgramCourseEnrollment
from super_csv.csv_processor import ValidationError

from bulk_grades import api


class BaseTests(TestCase):
    """
    Common setup functionality for all test cases
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.learner = User.objects.create(username='student@example.com')
        cls.staff = User.objects.create(username='staff@example.com')
        cls.usage_key = 'block-v1:testX+sg101+2019+type@sequential+block@homework_questions'
        cls.other_usage_key = 'block-v1:testX+sg101+2019+type@sequential+block@lab_questions'
        cls.course_id = 'course-v1:testX+sg101+2019'
        cls.learners = cls._make_enrollments()
        cls.audit_learner, cls.verified_learner, cls.masters_learner = cls.learners

    @classmethod
    def _make_enrollments(cls):
        return [cls._make_enrollment(name, name) for name in ['audit', 'verified', 'masters']]

    @classmethod
    def _make_enrollment(cls, name, mode):
        user = User.objects.create(username='%s@example.com' % name)
        Profile.objects.create(user=user, name=name)
        enroll = CourseEnrollment.objects.create(course_id=cls.course_id, user=user, mode=mode)
        if mode == 'masters':
            ProgramCourseEnrollment.objects.create(course_enrollment=enroll)
        return user

    def _mock_graded_subsections(self):
        """
        Helper function to define the return value of a mocked
        ``graded_subsections_for_course_id`` function.
        """
        return_value = []
        for usage_key in (self.usage_key, self.other_usage_key):
            _, _, block_id = usage_key.split('@')
            subsection = MagicMock()
            subsection.display_name = block_id.upper()
            subsection.location.block_id = block_id
            return_value.append(subsection)
        return return_value

    def _headers_for_subsections(self, subsections):
        """ For a list of subsections, return a list of headers (e.g. original subsection grade/ new override) """
        headers = []
        subsection_prefixes = ['name', 'grade', 'original_grade', 'previous_override', 'new_override']

        for short_id, prefix in product(subsections, subsection_prefixes):
            headers.append(f'{prefix}-{short_id}')

        return headers

    def _mock_result_data(self, subsections=['homework', 'lab_ques']):
        """
        Return some row data that mocks what would be loaded from an override history CSV
        """
        result_data = []

        for learner in self.learners:
            row = {
                'user_id': learner.id,
                'username': learner.username,
                'student_key': '',
                'course_id': self.course_id,
                'track': 'masters' if learner.username == 'masters@example.com' else 'audit',
                'cohort': '',
                'error': '',
                'status': 'No Action'
            }
            # Add subsection data
            for header in self._headers_for_subsections(subsections):
                row[header] = ''
            result_data.append(row)

        return result_data


class TestApi(BaseTests):
    """
    Tests of the api functions.
    """
    def test_set_score(self):
        api.set_score(self.usage_key, self.learner.id, 11, 22, override_user_id=self.staff.id)
        score = api.get_score(self.usage_key, self.learner.id)
        assert score['score'] == 11
        assert score['who_last_graded'] == self.staff.username
        api.set_score(UsageKey.from_string(self.usage_key), self.learner.id, 12, 22, override_user_id=self.staff.id)
        score = api.get_score(self.usage_key, self.learner.id)
        assert score['score'] == 12
        score = api.get_score(self.usage_key, 11)
        assert score is None

    def test_negative_score(self):
        with self.assertRaisesMessage(ValueError, 'score must be positive'):
            api.set_score(self.usage_key, self.learner.id, -2, 22, override_user_id=self.staff.id)


@ddt.ddt
class TestScoreProcessor(BaseTests):
    """
    Tests exercising the processing performed by ScoreCSVProcessor
    """
    def _get_row(self, **kwargs):
        """
        Get a properly shaped row
        """
        row = {
            'block_id': self.usage_key,
            'New Points': 0,
            'user_id': self.learner.id,
            'csum': '07ec',
            'Previous Points': '',
        }
        kwargs['New Points'] = kwargs.get('points', 0)
        row.update(kwargs)
        return row

    def test_export_no_prev_scores(self):
        processor = api.ScoreCSVProcessor(block_id=self.usage_key)
        rows = list(processor.get_iterator())
        assert len(rows) == 4
        processor = api.ScoreCSVProcessor(block_id=self.usage_key, track='masters')
        rows = list(processor.get_iterator())
        assert len(rows) == 2
        data = '\n'.join(rows)
        assert 'masters' in data
        assert 'ext:5' in data
        assert 'audit' not in data
        prev_points_index = rows[0].split(',').index('Previous Points')
        for row in rows[1:]:
            prev_points = row.split(',')[prev_points_index]
            assert prev_points == ''

    @ddt.data('somebody', api.UNKNOWN_LAST_SCORE_OVERRIDER)
    def test_export_prev_scores(self, expected_who_last_graded_value):
        with patch('bulk_grades.api.get_scores') as mocked_get_scores:
            mock_score_data = {
                'score': '100',
                'modified': datetime.datetime.now(),
                'who_last_graded': expected_who_last_graded_value,
            }
            mocked_get_scores.return_value = {
                learner.id: mock_score_data
                for learner in self.learners
            }

            processor = api.ScoreCSVProcessor(block_id=self.usage_key)
            rows = list(processor.get_iterator())

        assert len(rows) == 4
        column_names = rows[0].split(',')
        prev_points_index = column_names.index('Previous Points')
        who_last_graded_index = column_names.index('who_last_graded')

        for row in rows[1:]:
            row_data = row.split(',')
            assert row_data[prev_points_index] == '100.0'
            assert row_data[who_last_graded_index] == expected_who_last_graded_value

    def test_validate(self):
        processor = api.ScoreCSVProcessor(block_id=self.usage_key, max_points=100)
        processor.validate_row(self._get_row())
        processor.validate_row(self._get_row(points=1))
        processor.validate_row(self._get_row(points=50.0))
        with self.assertRaises(ValidationError):
            processor.validate_row(self._get_row(points=-5))
        with self.assertRaises(ValidationError):
            processor.validate_row(self._get_row(points=101))
        with self.assertRaises(ValidationError):
            processor.validate_row(self._get_row(points='ab'))
        with self.assertRaises(ValidationError):
            processor.validate_row(self._get_row(block_id=self.usage_key + 'b', csum='60aa'))
        with self.assertRaises(ValidationError):
            processor.validate_row(self._get_row(block_id=self.usage_key + 'b', csum='bad'))

    def test_preprocess(self):
        processor = api.ScoreCSVProcessor(block_id=self.usage_key, max_points=100)
        row = self._get_row(points=1)
        expected = {
            'user_id': row['user_id'],
            'block_id': processor.block_id,
            'new_points': 1.0,
            'max_points': processor.max_points,
            'override_user_id': processor.user_id
        }
        assert processor.preprocess_row(row) == expected
        # won't process duplicates
        assert not processor.preprocess_row(row)
        assert not processor.preprocess_row(self._get_row(points=0))

    def test_process(self):
        processor = api.ScoreCSVProcessor(block_id=self.usage_key, max_points=100)
        operation = processor.preprocess_row(self._get_row(points=1))
        assert processor.process_row(operation) == (True, None)
        processor.handle_undo = True
        assert processor.process_row(operation)[1]['score'] == 1


class MySubsectionClass(api.GradedSubsectionMixin):
    pass


class TestGradedSubsectionMixin(BaseTests):
    """
    Tests the shared methods defined in ``GradeSubsectionMixin``.
    """
    def setUp(self):
        super().setUp()
        self.instance = MySubsectionClass()

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_get_graded_subsections(self, mock_graded_subsections):
        mock_graded_subsections.return_value = self._mock_graded_subsections()
        subsections = self.instance._get_graded_subsections(self.course_id)  # pylint: disable=protected-access
        assert 2 == len(subsections)
        assert 'HOMEWORK_QUESTIONS' == subsections['homework'][1]
        assert 'LAB_QUESTIONS' == subsections['lab_ques'][1]

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_filter_subsection(self, mock_graded_subsections):
        mock_graded_subsections.return_value = self._mock_graded_subsections()
        filter_subsection = MagicMock()
        filter_subsection.block_id = 'lab_questions'
        subsections = self.instance._get_graded_subsections(  # pylint: disable=protected-access
            self.course_id,
            filter_subsection=filter_subsection
        )
        assert 1 == len(subsections)
        assert 'LAB_QUESTIONS' == subsections['lab_ques'][1]

    def test_subsection_column_names(self):
        short_subsection_ids = ['subsection-1', 'subsection-2', 'subsection-3']
        prefixes = ['grade', 'original_grade', 'previous_override', 'new_override']

        # pylint: disable=protected-access
        actual_column_names = self.instance._subsection_column_names(short_subsection_ids, prefixes)
        expected_column_names = [
            'grade-subsection-1',
            'original_grade-subsection-1',
            'previous_override-subsection-1',
            'new_override-subsection-1',
            'grade-subsection-2',
            'original_grade-subsection-2',
            'previous_override-subsection-2',
            'new_override-subsection-2',
            'grade-subsection-3',
            'original_grade-subsection-3',
            'previous_override-subsection-3',
            'new_override-subsection-3',
        ]
        assert expected_column_names == actual_column_names

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_duplicate_short_name_skipped(self, mock_graded_subsections):
        mocked_subsections = self._mock_graded_subsections()
        subsection = MagicMock()
        subsection.display_name = 'HOMEWORK_SOMETHING_ELSE'
        subsection.location.block_id = 'homework_something_else'
        mocked_subsections.append(subsection)
        mock_graded_subsections.return_value = mocked_subsections
        subsections = self.instance._get_graded_subsections(self.course_id)
        assert len(subsections) == 2


def make_mock_grade(override=None, earned_graded=1, possible_graded=1):
    return Mock(override=override, earned_graded=earned_graded, possible_graded=possible_graded)


def mock_subsection_grade(grade_iter):
    def f(user_id, course_key):
        result = {}
        for subsection in grades_api.graded_subsections_for_course_id(course_key):
            result[subsection.location] = next(grade_iter)
        return result
    return f


@ddt.ddt
class TestGradeProcessor(BaseTests):
    """
    Tests exercising the processing performed by GradeCSVProcessor
    """
    NUM_USERS = 3
    default_headers = ['user_id', 'username', 'student_key', 'course_id', 'track', 'cohort']

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read', return_value=Mock(percent=0.50))
    def test_export(self, course_grade_factory_mock):  # pylint: disable=unused-argument
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        rows = list(processor.get_iterator())
        # tests that there a 'student_key' column present
        assert any('student_key' in row for row in rows)
        # tests that a masters student has student_key populated
        masters_row = [row for row in rows if 'masters' in row]
        assert 'masters@example.com,ext:5,' in masters_row[0]
        # tests that a non-masters (verified) student does NOT have a student key populated
        verified_row = [row for row in rows if 'verified' in row]
        # note the null between the two commas, in place where student_key is supposed to be
        assert 'verified@example.com,,' in verified_row[0]
        assert len(rows) == self.NUM_USERS + 1

    @ddt.data(True, False)
    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read', return_value=Mock(percent=0.50))
    def test_export__inactive_learner(self, active_only, course_grade_factory_mock):  # pylint: disable=unused-argument
        # Create a learner, then get her PCE and deactivate it, which will deactivate the CourseEnrollment as well
        inactive_learner = User.objects.create(username='inactive_learner')
        Profile.objects.create(user=inactive_learner, name="Ina Ctive-Learner")
        course_enrollment = CourseEnrollment.objects.create(
            course_id=self.course_id,
            user=inactive_learner,
            mode='masters'
        )
        ProgramCourseEnrollment.objects.create(course_enrollment=course_enrollment)
        
        course_enrollment.is_active = False
        course_enrollment.save()

        # Get csv file and grab row 
        processor = api.GradeCSVProcessor(course_id=self.course_id, active_only=active_only)
        rows = list(processor.get_iterator())
        inactive_row = [row for row in rows if 'inactive_learner' in row]

        if active_only:
            # Assert that inactive learner is not present is CSV
            assert len(inactive_row) == 0
        else:
            # Assert that inactive learner is still present in the CSV export
            assert len(inactive_row) == 1
            assert 'inactive_learner,ext:6' in inactive_row[0]

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_columns_not_duplicated_during_init(self, mock_graded_subsections):
        """
        Tests that GradeCSVProcessor.__init__() does not cause
        column names to be duplicated.
        """
        mock_graded_subsections.return_value = self._mock_graded_subsections()
        processor_1 = api.GradeCSVProcessor(course_id=self.course_id)

        # pretend that we serialize the processor data to some "state"
        state = deepcopy(processor_1.__dict__)
        processor_2 = api.GradeCSVProcessor(**state)

        assert processor_1.columns == processor_2.columns
        expected_columns = [
            *self.default_headers,
            'name-homework',
            'grade-homework',
            'original_grade-homework',
            'previous_override-homework',
            'new_override-homework',
            'name-lab_ques',
            'grade-lab_ques',
            'original_grade-lab_ques',
            'previous_override-lab_ques',
            'new_override-lab_ques',
        ]
        assert expected_columns == processor_1.columns

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_subsection_max_min(self, mock_graded_subsections):
        mock_graded_subsections.return_value = self._mock_graded_subsections()
        # should filter out everything; all grades are 1 from mock_apps grades api
        processor = api.GradeCSVProcessor(course_id=self.course_id, subsection=self.usage_key, subsection_grade_max=50)
        rows = list(processor.get_iterator())
        assert len(rows) != self.NUM_USERS + 1
        # should filter out all but 1
        processor = api.GradeCSVProcessor(course_id=self.course_id, subsection=self.usage_key, subsection_grade_max=80)
        with patch('lms.djangoapps.grades.api.get_subsection_grades') as mock_subsection_grades:
            with patch('lms.djangoapps.grades.api.CourseGradeFactory.read') as mock_course_grade:
                mock_course_grade.return_value = Mock(percent=1)
                mock_subsection_grades.side_effect = mock_subsection_grade(
                    chain([make_mock_grade(earned_graded=0.5)], repeat(make_mock_grade()))
                )
                rows = list(processor.get_iterator())
        assert len(rows) == 2

        processor = api.GradeCSVProcessor(course_id=self.course_id, subsection=self.usage_key, subsection_grade_min=200)
        rows = list(processor.get_iterator())
        assert len(rows) != self.NUM_USERS + 1

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_subsection_max_min_no_subsection_grade(self, mock_graded_subsections):
        mock_graded_subsections.return_value = self._mock_graded_subsections()
        processor = api.GradeCSVProcessor(course_id=self.course_id, subsection=self.usage_key, subsection_grade_max=101)
        with patch('lms.djangoapps.grades.api.get_subsection_grades') as mock_subsection_grades:
            with patch('lms.djangoapps.grades.api.CourseGradeFactory.read') as mock_course_grade:
                mock_course_grade.return_value = Mock(percent=1)
                mock_subsection_grades.side_effect = mock_subsection_grade(chain([None], repeat(make_mock_grade())))
                rows = list(processor.get_iterator())
        assert len(rows) == self.NUM_USERS

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_no_subsection_grade(self, mock_graded_subsections):
        mock_graded_subsections.return_value = self._mock_graded_subsections()
        processor = api.GradeCSVProcessor(course_id=self.course_id, subsection=self.usage_key)
        with patch('lms.djangoapps.grades.api.get_subsection_grades') as mock_subsection_grades:
            with patch('lms.djangoapps.grades.api.CourseGradeFactory.read') as mock_course_grade:
                mock_course_grade.return_value = Mock(percent=1)
                mock_subsection_grades.side_effect = mock_subsection_grade(chain([None], repeat(make_mock_grade())))
                rows = list(processor.get_iterator())
        assert len(rows) == self.NUM_USERS +1
        grade_column_index = rows[0].split(',').index('original_grade-homework')
        row = rows[1].split(',')
        assert row[grade_column_index] == ''

        for i in (2, 3):
            row = rows[i].split(',')
            assert row[grade_column_index] == '1'

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read')
    def test_course_grade_filters(self, course_grade_factory_mock):
        course_grade_factory_mock.side_effect = cycle((Mock(percent=0.50), Mock(percent=0.70), Mock(percent=0.90)))

        processor = api.GradeCSVProcessor(course_id=self.course_id, max_points=100, course_grade_min=50)
        rows = list(processor.get_iterator())
        self.assertEqual(len(rows), self.NUM_USERS+1)

        processor = api.GradeCSVProcessor(course_id=self.course_id, max_points=100, course_grade_min=60, course_grade_max=80)
        rows = list(processor.get_iterator())
        self.assertEqual(len(rows), (self.NUM_USERS - 2)+1)

    def test_preprocess_negative_number_error(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        row = {
            'block_id': self.usage_key,
            'new_override-123402db': '1',
            'new_override-85bb02db': '-1',
            'user_id': self.learner.id,
            'csum': '07ec',
            'Previous Points': '',
        }
        with self.assertRaisesMessage(ValidationError, 'Grade must not be negative'):
            processor.preprocess_row(row)

    def test_preprocess_nan_error(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        row = {
            'block_id': self.usage_key,
            'new_override-123402db': '1',
            'new_override-85bb02db': 'not a number',
            'user_id': self.learner.id,
            'csum': '07ec',
            'Previous Points': '',
        }
        with self.assertRaisesMessage(ValueError, 'Grade must be a number'):
            processor.preprocess_row(row)

    def test_multiple_subsection_override(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        row = {
            'block_id': self.usage_key,
            'new_override-12f402db': '3',
            'new_override-123402db': '2',
            'new_override-85bb02db': '1',
            'user_id': self.learner.id,
            'Previous Points': '',
        }
        operation = processor.preprocess_row(row)
        assert operation
        # 3 grades are getting override
        assert len(operation['new_override_grades']) == 3

    def test_repeat_user(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        username = 'ditto'
        row = {
            'block_id': self.usage_key,
            'new_override-85bb02db': '1',
            'user_id': self.learner.id,
            'username': username,
            'Previous Points': '',
        }
        operation = processor.preprocess_row(row)
        assert operation
        
        row2 = {
            'block_id': self.usage_key,
            'new_override-123402db': '2',
            'user_id': self.learner.id,
            'username': username,
            'Previous Points': ''
        }

        # different row with the same user id throw error
        with self.assertRaisesMessage(ValidationError, 'Repeated user'):
            processor.preprocess_row(row2)
            # there should be 2 errors for the first repeat user error
            self.assertCountEqual(len(processor.error_messages), 2)
        
        with self.assertRaisesMessage(ValidationError, 'Repeated user'):
            processor.preprocess_row(row2)
            # there should be 3 errors for the second repeat user error
            self.assertCountEqual(len(processor.error_messages), 3)

    def test_empty_grade(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        row = {
            'block_id': self.usage_key,
            'new_override-123402db': '   ',
            'new_override-85bb02db': '   ',
            'user_id': self.learner.id,
            'Previous Points': '',
        }
        operation = processor.preprocess_row(row)
        assert len(operation['new_override_grades']) == 0

    def test_validate_row(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        row = {
            'block_id': self.usage_key,
            'new_override-123402db': '2',
            'new_override-85bb02db': '1',
            'user_id': self.learner.id,
            'Previous Points': '',
            'course_id': self.course_id,
        }
        processor.validate_row(row)
        row['course_id'] = 'something else'
        with self.assertRaisesMessage(ValidationError, 'Wrong course id'):
            processor.validate_row(row)\

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_process_file(self, mock_graded_subsections):
        mock_graded_subsections.return_value = self._mock_graded_subsections()
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        mock_csv_data = {
            'user_id': self.learner.id,
            'username': self.learner.username,
            'course_id': self.course_id,
            'track': None,
            'cohort': None,
            'name-homework': 'Homework',
            'original_grade-homework': 0,
            'previous_override-homework': None,
            'new_override-homework': 1,
            'name-lab_ques': 'Lab',
            'original_grade-lab_ques': 0,
            'previous_override-lab_ques': None,
            'new_override-lab_ques': 2,
        }
        mock_csv = ','.join(mock_csv_data.keys())
        mock_csv += '\n'
        mock_csv += ','.join('' if v is None else str(v) for v in mock_csv_data.values())
        buf = ContentFile(mock_csv.encode('utf-8'))
        processor.process_file(buf)

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read')
    @patch('lms.djangoapps.grades.api.get_subsection_grades')
    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_assignment_grade(self, mocked_graded_subsections, mocked_get_subsection_grades, mocked_course_grade):
        # Two mock graded subsections
        mock_graded_subsections = self._mock_graded_subsections()
        mocked_graded_subsections.return_value = mock_graded_subsections

        # For each user, we want a subsection with:
        #  - an original_grade, but no override
        #  - an original_grade and an override
        mocked_get_subsection_grades.side_effect = mock_subsection_grade(
            cycle([
                make_mock_grade(earned_graded=3, possible_graded=5),
                make_mock_grade(earned_graded=3, possible_graded=5, override=Mock(earned_graded_override=5))
            ])
        )

        # We need to mock the course grade or everything will explode
        mocked_course_grade.return_value = Mock(percent=1)

        processor = api.GradeCSVProcessor(course_id=self.course_id)
        rows = list(processor.get_iterator())
        headers = rows[0].strip().split(',')
        # Massage data into a list of dicts, keyed on column header
        table = [
            {header: user_row_val for header, user_row_val in zip(headers, user_row.strip().split(','))}
            for user_row in rows[1:]
        ]

        # If there's an override, use that, if not, use the original grade
        for learner_data_row in table:
            assert learner_data_row['grade-homework'] == '3'
            assert learner_data_row['original_grade-homework'] == '3'
            assert learner_data_row['previous_override-homework'] == ''
            assert learner_data_row['grade-lab_ques'] == '5'
            assert learner_data_row['original_grade-lab_ques'] == '3'
            assert learner_data_row['previous_override-lab_ques'] == '5'

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_filter_override_history_columns(self, mocked_graded_subsections):
        # Given 2 graded subsections ...
        mocked_graded_subsections.return_value = self._mock_graded_subsections()
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        processor.result_data = self._mock_result_data()

        # One of which, "homework", was overridden for 2 students
        processor.result_data[0].update({'new_override-homework': '1', 'status': 'Success'})
        processor.result_data[2].update({'new_override-homework': '2', 'status': 'Success'})

        # When columns are filtered and I request a copy of the report
        processor.columns = processor.filtered_column_headers()
        rows = list(processor.get_iterator(error_data='1'))

        # Then my headers include the modified subsection headers, and exclude the unmodified section
        headers = rows[0].strip().split(',')
        expected_headers = [
            *self.default_headers,
            'name-homework',
            'grade-homework',
            'original_grade-homework',
            'previous_override-homework',
            'new_override-homework',
            'status',
            'error']

        assert headers == expected_headers
        assert len(rows) == self.NUM_USERS + 1

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_filter_override_history_limited_columns(self, mocked_graded_subsections):
        # Given a set of overrides
        mocked_graded_subsections.return_value = self._mock_graded_subsections()
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        processor.result_data = self._mock_result_data()

        processor.result_data[0].update({'new_override-lab_ques': '1', 'status': 'Success'})
        processor.result_data[2].update({'new_override-lab_ques': '2', 'status': 'Success'})

        # Where some subsection were not included in the original override
        for row in processor.result_data:
            row.pop('name-homework')
            row.pop('original_grade-homework')
            row.pop('previous_override-homework')
            row.pop('new_override-homework')

        # When columns are filtered and I request a copy of the report
        processor.columns = processor.filtered_column_headers()
        rows = list(processor.get_iterator(error_data='1'))

        # Then my headers include the correct subsections (and don't crash like they used to)
        headers = rows[0].strip().split(',')
        expected_headers = [
            *self.default_headers,
            'name-lab_ques',
            'grade-lab_ques',
            'original_grade-lab_ques',
            'previous_override-lab_ques',
            'new_override-lab_ques',
            'status',
            'error'
        ]

        assert headers == expected_headers
        assert len(rows) == self.NUM_USERS + 1

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_filter_override_history_noop(self, mocked_graded_subsections):
        # Given no overrides for a given report
        mocked_graded_subsections.return_value = self._mock_graded_subsections()
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        processor.result_data = self._mock_result_data()

        # When columns are filtered and I request a copy of the report
        processor.columns = processor.filtered_column_headers()
        rows = list(processor.get_iterator(error_data='1'))

        # Then my headers don't include any subsections
        headers = rows[0].strip().split(',')
        expected_headers = [
            *self.default_headers,
            'status',
            'error']

        assert headers == expected_headers
        assert len(rows) == self.NUM_USERS + 1
        
    def _process_iterator(self, iterator):
        """ 
        Given the csv processor iterator, return a list of dicts.
        Each dict corresponds to a data row in the returned data with each column keyed by it's header

        raises ValueError if there is a length mismatch
        """  
        headers = next(iterator).strip().split(',')
        result = []
        for row in iterator:
            row = row.strip().split(',')
            if len(row) != len(headers):
                raise ValueError("Mismatched csv column lengths")
            row_dict = {}
            for i, cell in enumerate(row):
                row_dict[headers[i]] = cell
            result.append(row_dict)
        return result


    @ddt.unpack
    @ddt.data(
        ([], True, True),
        (['all'], False, False),
        (['role_a'], False, True),
        (['role_b'], True, False),
        (['role_a', 'role_b'], False, False),
        (['nonexistent-role'], True, True),
    )
    def test_filter_course_roles(
        self,
        excluded_course_roles,
        expect_role_a,
        expect_role_b,
    ):
        processor = api.GradeCSVProcessor(course_id=self.course_id, excluded_course_roles=excluded_course_roles)

        # Give audit_learner the role role_a and verified_learner the role role_b
        CourseAccessRole.objects.create(
            user=self.audit_learner,
            course_id=self.course_id,
            role="role_a",
        )
        CourseAccessRole.objects.create(
            user=self.verified_learner,
            course_id=self.course_id,
            role="role_b",
        )
        with patch('lms.djangoapps.grades.api.graded_subsections_for_course_id') as mock_subsections:
            with patch('lms.djangoapps.grades.api.CourseGradeFactory.read') as mock_course_grade:
                mock_subsections.return_value = self._mock_graded_subsections()
                mock_course_grade.return_value = Mock(percent=0.50)
                data = self._process_iterator(processor.get_iterator())
        
        usernames = {row['username'] for row in data}
        self.assertEqual(self.audit_learner.username in usernames, expect_role_a)
        self.assertEqual(self.verified_learner.username in usernames, expect_role_b)

    def test_filter_course_roles__role_in_another_course(self):
        role_to_filter = 'role-to-filter'
        another_course = "course-v1:testX+sg201+2021"
        # Give audit_learner the role `role_to_filter` and verified_learner the role `some_other_role`
        CourseAccessRole.objects.create(
            user=self.audit_learner,
            course_id=self.course_id,
            role=role_to_filter,
        )
        CourseAccessRole.objects.create(
            user=self.verified_learner,
            course_id=self.course_id,
            role="some_other_role",
        ) 

        # Enroll verified_learner in `another_course` and assign them `role_to_filter` in that course
        CourseEnrollment.objects.create(course_id=another_course, user=self.verified_learner, mode='audit')
        CourseAccessRole.objects.create(
            user=self.verified_learner,
            course_id=another_course,
            role=role_to_filter,
        )

        with patch('lms.djangoapps.grades.api.graded_subsections_for_course_id') as mock_subsections:
            with patch('lms.djangoapps.grades.api.CourseGradeFactory.read') as mock_course_grade:
                mock_subsections.return_value = self._mock_graded_subsections()
                mock_course_grade.return_value = Mock(percent=0.50)
                processor = api.GradeCSVProcessor(course_id=self.course_id, excluded_course_roles=[role_to_filter])
                data = self._process_iterator(processor.get_iterator())

        # Verified learner should still be present, because their excluded role is for a different course
        usernames = {row['username'] for row in data}
        self.assertFalse(self.audit_learner.username in usernames)
        self.assertTrue(self.verified_learner.username in usernames)


@ddt.ddt
class TestInterventionProcessor(BaseTests):
    """
    Tests exercising the processing performed by InterventionCSVProcessor
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.grade_factory_patcher = patch('lms.djangoapps.grades.api.CourseGradeFactory.read')
        cls.learner_api_client_patcher = patch('bulk_grades.api.LearnerAPIClient')
        cls.mocked_learner_api_client = cls.learner_api_client_patcher.start()

        user_data = {'videos_overall': 12, 'videos_last_week': 0, 'problems_overall': 10,
                                        'problems_last_week': 5,
                                        'correct_problems_overall': 66, 'correct_problems_last_week': 44,
                                        'problems_attempts_overall': 233, 'problems_attempts_last_week': 221,
                                        'correct_problems_attempts_overall': 101,
                                        'correct_problems_attempts_last_week': 99,
                                        'forum_posts_overall': 2, 'forum_posts_last_week': 0, 'date_last_active': 2}
        cls.data = {
                'audit@example.com': {'videos_overall': 2, 'videos_last_week': 0, 'problems_overall': 10,
                                      'problems_last_week': 5,
                                      'correct_problems_overall': 66, 'correct_problems_last_week': 44,
                                      'problems_attempts_overall': 233, 'problems_attempts_last_week': 221,
                                      'correct_problems_attempts_overall': 101, 'correct_problems_attempts_last_week': 99,
                                      'forum_posts_overall': 2, 'forum_posts_last_week': 0, 'date_last_active': 2},
                'masters@example.com': user_data,
                'masters2@example.com': user_data,
        }
        cls.mocked_learner_api_client.return_value.courses.return_value.intervention.return_value.get.return_value = \
            cls.data
        cls.masters_learner_2 = cls._make_enrollment('masters2', 'masters')

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.learner_api_client_patcher.stop()

    def setUp(self):
        super().setUp()
        self.mocked_course_grade_factory = self.grade_factory_patcher.start()
        course_grade_mock = Mock(percent=0.5, letter_grade='A')
        self.mocked_course_grade_factory.return_value = course_grade_mock

    def tearDown(self):
        super().tearDown()
        self.grade_factory_patcher.stop()

    def test_export(self):
        processor = api.InterventionCSVProcessor(course_id=self.course_id)
        rows = list(processor.get_iterator())
        assert len(rows) == 3

    @ddt.data(
        (10, 99, 3),
        (70, 99, 2),
        (10, 80, 2),
        (70, 80, 1),
    )
    @ddt.unpack
    def test_filter_course_grade(self, course_grade_min, course_grade_max, expected_rows):
        grade_mock_A = Mock(percent=0.9)
        grade_mock_D = Mock(percent=0.5)
        self.mocked_course_grade_factory.side_effect = (grade_mock_A, grade_mock_D)
        processor = api.InterventionCSVProcessor(
            course_id=self.course_id,
            course_grade_min=course_grade_min,
            course_grade_max=course_grade_max,
        )
        rows = list(processor.get_iterator())
        assert len(rows) == expected_rows

    @ddt.data(
        (10, 99, 3),
        (70, 99, 2),
        (10, 80, 2),
        (70, 80, 1),
    )
    @ddt.unpack
    def test_filter_subsection_grade(self, subsection_grade_min, subsection_grade_max, expected_rows):
        subsections = list(grades_api.graded_subsections_for_course_id(None))
        filter_subsection = subsections[1]
        processor = api.InterventionCSVProcessor(
            course_id=self.course_id,
            subsection=str(filter_subsection.location),
            subsection_grade_min=subsection_grade_min,
            subsection_grade_max=subsection_grade_max,
        )
        grades = [
            make_mock_grade(), make_mock_grade(earned_graded=0.9), make_mock_grade(),
            make_mock_grade(), make_mock_grade(earned_graded=0.5), make_mock_grade(),
        ]
        with patch('lms.djangoapps.grades.api.get_subsection_grades') as mock_subsection_grades:
            mock_subsection_grades.side_effect = mock_subsection_grade(iter(grades))
            rows = list(processor.get_iterator())

        assert len(rows) == expected_rows

    def test_filter_subsection_grade_no_subsection_grade(self):
        subsections = list(grades_api.graded_subsections_for_course_id(None))
        filter_subsection = subsections[1]
        processor = api.InterventionCSVProcessor(
            course_id=self.course_id,
            subsection=str(filter_subsection.location),
            subsection_grade_min=1,
            subsection_grade_max=100,
        )
        grades = chain([make_mock_grade(), None], repeat(make_mock_grade()))
        with patch('lms.djangoapps.grades.api.get_subsection_grades') as mock_subsection_grades:
            mock_subsection_grades.side_effect = mock_subsection_grade(grades)
            rows = list(processor.get_iterator())

        assert len(rows) == 2
        row = rows[1].split(',')
        assert row[0] == str(self.masters_learner_2.id)

    def test_export_no_subsection_grade(self):
        subsections = list(grades_api.graded_subsections_for_course_id(None))
        subsection = subsections[1]
        processor = api.InterventionCSVProcessor(course_id=self.course_id)
        grades = chain([make_mock_grade(), None], repeat(make_mock_grade()))
        with patch('lms.djangoapps.grades.api.get_subsection_grades') as mock_subsection_grades:
            mock_subsection_grades.side_effect = mock_subsection_grade(grades)
            rows = list(processor.get_iterator())

        grade_column_index = rows[0].split(',').index('grade-123402db')

        assert len(rows) == 3

        row = rows[1].split(',')
        assert row[0] == str(self.masters_learner.id)
        assert row[grade_column_index] == ''

        row = rows[2].split(',')
        assert row[0] == str(self.masters_learner_2.id)
        assert row[grade_column_index] == '1'

    def test_export_override(self):
        subsections = list(grades_api.graded_subsections_for_course_id(None))
        subsection = subsections[1]
        processor = api.InterventionCSVProcessor(course_id=self.course_id)
        mock_grade = make_mock_grade(override=Mock(earned_graded_override=.9), earned_graded=.4)
        grades = chain((make_mock_grade(), mock_grade), repeat(make_mock_grade()))
        with patch('lms.djangoapps.grades.api.get_subsection_grades') as mock_subsection_grades:
            mock_subsection_grades.side_effect = mock_subsection_grade(grades)
            rows = list(processor.get_iterator())

        grade_column_index = rows[0].split(',').index('grade-123402db')

        assert len(rows) == 3

        row = rows[1].split(',')
        assert row[0] == str(self.masters_learner.id)
        assert row[grade_column_index] == '0.9'
