#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests for the `edx-bulk-grades` api module.
"""
from __future__ import absolute_import, unicode_literals

from copy import deepcopy

from django.contrib.auth.models import User
from django.test import TestCase
from mock import MagicMock, Mock, patch
from super_csv.csv_processor import ValidationError

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
        cls._make_enrollments()

    @classmethod
    def _make_enrollments(cls):
        for name in ['audit', 'verified', 'masters']:
            user = User.objects.create(username='%s@example.com' % name)
            Profile.objects.create(user=user, name=name)
            enroll = CourseEnrollment.objects.create(course_id=cls.course_id, user=user, mode=name)
            if name == 'masters':
                ProgramCourseEnrollment.objects.create(course_enrollment=enroll)

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
        score = api.get_score(self.usage_key, 11)
        assert score is None

    def test_negative_score(self):
        with self.assertRaisesMessage(ValueError, 'score must be positive'):
            api.set_score(self.usage_key, self.learner.id, -2, 22, override_user_id=self.staff.id)


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

    def test_export(self):
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

    def test_validate(self):
        processor = api.ScoreCSVProcessor(block_id=self.usage_key, max_points=100)
        processor.validate_row(self._get_row())
        processor.validate_row(self._get_row(points=1))
        processor.validate_row(self._get_row(points=50.0))
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

        processor = api.GradeCSVProcessor(course_id=self.course_id, subsection=self.usage_key, subsection_grade_min=200)
        rows = list(processor.get_iterator())
        assert len(rows) != self.NUM_USERS + 1

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read')
    def test_course_grade_filters(self, course_grade_factory_mock):
        course_grade_factory_mock.return_value = Mock(percent=0.50)

        processor = api.GradeCSVProcessor(course_id=self.course_id, max_points=100, course_grade_min=60)
        rows = list(processor.get_iterator())
        self.assertNotEqual(len(rows), self.NUM_USERS+1)

        processor = api.GradeCSVProcessor(course_id=self.course_id, max_points=100, course_grade_min=10, course_grade_max=60)
        rows = list(processor.get_iterator())
        self.assertEqual(len(rows), self.NUM_USERS+1)

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read')
    def test_less_than_zero(self, course_grade_factory_mock):
        course_grade_factory_mock.return_value = Mock(percent=0.50)
        processor = api.GradeCSVProcessor(course_id=self.course_id)

        row = {
            'block_id': self.usage_key,
            'new_override-block-v1': '-1',
            'user_id': self.learner.id,
            'csum': '07ec',
            'Previous Points': '',
        }
        with self.assertRaisesMessage(ValidationError, 'Grade must be positive'):
            processor.preprocess_row(row)


class TestInterventionProcessor(BaseTests):
    """
    Tests exercising the processing performed by InterventionCSVProcessor
    """

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read')
    @patch('bulk_grades.api.LearnerAPIClient')
    def test_export(self, mocked_api, mocked_course_grade_factory):
        data = {
                'audit@example.com': {'videos_overall': 2, 'videos_last_week': 0, 'problems_overall': 10,
                                      'problems_last_week': 5,
                                      'correct_problems_overall': 66, 'correct_problems_last_week': 44,
                                      'problems_attempts_overall': 233, 'problems_attempts_last_week': 221,
                                      'correct_problems_attempts_overall': 101, 'correct_problems_attempts_last_week': 99,
                                      'forum_posts_overall': 2, 'forum_posts_last_week': 0, 'date_last_active': 2},
                'masters@example.com': {'videos_overall': 12, 'videos_last_week': 0, 'problems_overall': 10,
                                        'problems_last_week': 5,
                                        'correct_problems_overall': 66, 'correct_problems_last_week': 44,
                                        'problems_attempts_overall': 233, 'problems_attempts_last_week': 221,
                                        'correct_problems_attempts_overall': 101,
                                        'correct_problems_attempts_last_week': 99,
                                        'forum_posts_overall': 2, 'forum_posts_last_week': 0, 'date_last_active': 2}
        }
        mocked_api.return_value.courses.return_value.intervention.return_value.get.return_value = \
            data
        course_grade_mock = Mock(percent=0.5, letter_grade='A')
        mocked_course_grade_factory.return_value = course_grade_mock
        processor = api.InterventionCSVProcessor(course_id=self.course_id)
        rows = list(processor.get_iterator())
        assert len(rows) == 2
