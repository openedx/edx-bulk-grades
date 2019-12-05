#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests for the `edx-bulk-grades` api module.
"""
from __future__ import absolute_import, unicode_literals

import datetime
from copy import deepcopy
from itertools import chain, cycle, repeat

import ddt
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase
from mock import MagicMock, Mock, patch
from opaque_keys.edx.keys import UsageKey
from super_csv.csv_processor import ValidationError

import lms.djangoapps.grades.api as grades_api
from bulk_grades import api
from student.models import CourseEnrollment, Profile, ProgramCourseEnrollment


class BaseTests(TestCase):
    """
    Common setup functionality for all test cases
    """
    @classmethod
    def setUpTestData(cls):
        super(BaseTests, cls).setUpTestData()
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
        super(TestGradedSubsectionMixin, self).setUp()
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
        prefixes = ['original_grade', 'previous_override', 'new_override']

        # pylint: disable=protected-access
        actual_column_names = self.instance._subsection_column_names(short_subsection_ids, prefixes)
        expected_column_names = [
            'original_grade-subsection-1',
            'previous_override-subsection-1',
            'new_override-subsection-1',
            'original_grade-subsection-2',
            'previous_override-subsection-2',
            'new_override-subsection-2',
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


class TestGradeProcessor(BaseTests):
    """
    Tests exercising the processing performed by GradeCSVProcessor
    """
    NUM_USERS = 3

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read', return_value=Mock(percent=0.50))
    def test_export(self, course_grade_factory_mock):  # pylint: disable=unused-argument
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        rows = list(processor.get_iterator())
        assert len(rows) == self.NUM_USERS + 1

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
            'user_id',
            'username',
            'course_id',
            'track',
            'cohort',
            'name-homework',
            'original_grade-homework',
            'previous_override-homework',
            'new_override-homework',
            'name-lab_ques',
            'original_grade-lab_ques',
            'previous_override-lab_ques',
            'new_override-lab_ques'
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

    def test_preprocess_error(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        row = {
            'block_id': self.usage_key,
            'new_override-85bb02db': '-1',
            'user_id': self.learner.id,
            'csum': '07ec',
            'Previous Points': '',
        }
        with self.assertRaisesMessage(ValidationError, 'Grade must be positive'):
            processor.preprocess_row(row)
        row['new_override-85bb02db'] = 'not a number'
        with self.assertRaisesMessage(ValidationError, 'Grade must be a number'):
            processor.preprocess_row(row)

    def test_repeat_user(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        row = {
            'block_id': self.usage_key,
            'new_override-85bb02db': '1',
            'user_id': self.learner.id,
            'Previous Points': '',
        }
        operation = processor.preprocess_row(row)
        assert len(operation) == 4
        operation = processor.preprocess_row(row)
        assert len(operation) == 0

    def test_empty_grade(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        row = {
            'block_id': self.usage_key,
            'new_override-85bb02db': '   ',
            'user_id': self.learner.id,
            'Previous Points': '',
        }
        operation = processor.preprocess_row(row)
        assert len(operation) == 0

    def test_validate_row(self):
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        row = {
            'block_id': self.usage_key,
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


@ddt.ddt
class TestInterventionProcessor(BaseTests):
    """
    Tests exercising the processing performed by InterventionCSVProcessor
    """

    @classmethod
    def setUpClass(cls):
        super(TestInterventionProcessor, cls).setUpClass()
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
        super(TestInterventionProcessor, cls).tearDownClass()
        cls.learner_api_client_patcher.stop()

    def setUp(self):
        super(TestInterventionProcessor, self).setUp()
        self.mocked_course_grade_factory = self.grade_factory_patcher.start()
        course_grade_mock = Mock(percent=0.5, letter_grade='A')
        self.mocked_course_grade_factory.return_value = course_grade_mock

    def tearDown(self):
        super(TestInterventionProcessor, self).tearDown()
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
