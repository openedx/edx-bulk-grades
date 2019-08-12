#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests for the `edx-bulk-grades` api module.
"""

from __future__ import absolute_import, unicode_literals

from django.contrib.auth.models import User
from django.test import TestCase
from mock import MagicMock, patch
from super_csv.csv_processor import ValidationError

from bulk_grades import api
from student.models import CourseEnrollment, Profile, ProgramCourseEnrollment


class BaseTests(TestCase):
    """
    Common setup functionality for all test cases
    """
    def setUp(self):
        super(BaseTests, self).setUp()
        self.learner = User.objects.create(username='student@example.com')
        self.staff = User.objects.create(username='staff@example.com')
        self.block_id_in_module_id = '85bb02dbd2c14ba5bc31a0264b140dda'
        self.block_id = 'block-v1:testX+sg101+2019+type@test+block@%s' % self.block_id_in_module_id
        self.course_id = 'course-v1:testX+sg101+2019'

    def _make_enrollments(self):
        for name in ['audit', 'verified', 'masters']:
            user = User.objects.create(username='%s@example.com' % name)
            Profile.objects.create(user=user, name=name)
            enroll = CourseEnrollment.objects.create(course_id=self.course_id, user=user, mode=name)
            if name == 'masters':
                ProgramCourseEnrollment.objects.create(course_enrollment=enroll)


class TestApi(BaseTests):
    """
    Tests of the api functions.
    """
    def test_set_score(self):
        api.set_score(self.block_id, self.learner.id, 11, 22, override_user_id=self.staff.id)
        score = api.get_score(self.block_id, self.learner.id)
        assert score['score'] == 11
        assert score['who_last_graded'] == self.staff.username
        score = api.get_score(self.block_id, 11)
        assert score is None


class TestScoreProcessor(BaseTests):
    """
    Tests exercising the processing performed by ScoreCSVProcessor
    """
    def _get_row(self, **kwargs):
        """
        Get a properly shaped row
        """
        row = {
            'block_id': self.block_id,
            'New Points': 0,
            'user_id': self.learner.id,
            'csum': '07ec',
            'Previous Points': '',
        }
        kwargs['New Points'] = kwargs.get('points', 0)
        row.update(kwargs)
        return row

    def test_export(self):
        self._make_enrollments()
        processor = api.ScoreCSVProcessor(block_id=self.block_id)
        rows = list(processor.get_iterator())
        assert len(rows) == 4
        processor = api.ScoreCSVProcessor(block_id=self.block_id, track='masters')
        rows = list(processor.get_iterator())
        assert len(rows) == 2
        data = '\n'.join(rows)
        assert 'masters' in data
        assert 'ext:5' in data
        assert 'audit' not in data

    def test_validate(self):
        processor = api.ScoreCSVProcessor(block_id=self.block_id, max_points=100)
        processor.validate_row(self._get_row())
        processor.validate_row(self._get_row(points=1))
        processor.validate_row(self._get_row(points=50.0))
        with self.assertRaises(ValidationError):
            processor.validate_row(self._get_row(points=101))
        with self.assertRaises(ValidationError):
            processor.validate_row(self._get_row(points='ab'))
        with self.assertRaises(ValidationError):
            processor.validate_row(self._get_row(block_id=self.block_id + 'b', csum='60aa'))
        with self.assertRaises(ValidationError):
            processor.validate_row(self._get_row(block_id=self.block_id + 'b', csum='bad'))

    def test_preprocess(self):
        processor = api.ScoreCSVProcessor(block_id=self.block_id, max_points=100)
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
        processor = api.ScoreCSVProcessor(block_id=self.block_id, max_points=100)
        operation = processor.preprocess_row(self._get_row(points=1))
        assert processor.process_row(operation) == (True, None)
        processor.handle_undo = True
        assert processor.process_row(operation)[1]['score'] == 1


class TestGradeProcessor(BaseTests):
    """
    Tests exercising the processing performed by GradeCSVProcessor
    """
    NUM_USERS = 3

    def test_export(self):
        self._make_enrollments()
        processor = api.GradeCSVProcessor(course_id=self.course_id)
        rows = list(processor.get_iterator())
        assert len(rows) == self.NUM_USERS + 1

    @patch('lms.djangoapps.grades.api.graded_subsections_for_course_id')
    def test_subsection_max_min(self, mock_graded_subsections):
        self._make_enrollments()
        subsection = MagicMock()
        subsection.location = MagicMock()
        subsection.display_name = 'asdf'
        subsection.location.block_id = self.block_id_in_module_id
        mock_graded_subsections.return_value = [subsection]
        # should filter out everything; all grades are 1 from mock_apps grades api
        processor = api.GradeCSVProcessor(course_id=self.course_id, subsection=self.block_id, subsection_grade_max=50)
        rows = list(processor.get_iterator())
        assert len(rows) != self.NUM_USERS + 1

        processor = api.GradeCSVProcessor(course_id=self.course_id, subsection=self.block_id, subsection_grade_min=200)
        rows = list(processor.get_iterator())
        assert len(rows) != self.NUM_USERS + 1

