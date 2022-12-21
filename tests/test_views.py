""" Tests for bulk grade views """
from unittest.mock import Mock, patch

import lms.djangoapps.grades.api as grades_api
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from student.models import CourseAccessRole, CourseEnrollment, Profile, ProgramCourseEnrollment

from bulk_grades.api import GradeCSVProcessor


class ViewTestsMixin:
    """ Mixin for common test setup """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.password = 'password'
        cls.staff = User.objects.create(username='staff@example.com', password=cls.password)
        cls.course_id = 'course-v1:testX+sg101+2019'
        cls.subsections = list(grades_api.graded_subsections_for_course_id(cls.course_id))
        cls.subsection_short_ids = [subsection.location.block_id[:8] for subsection in cls.subsections]
        cls.learners = cls._make_enrollments()
        cls.audit_learner, cls.verified_learner, cls.masters_learner = cls.learners

    @classmethod
    def _make_enrollments(cls):
        return [cls._make_enrollment(name, name) for name in ['audit', 'verified', 'masters']]

    @classmethod
    def _make_enrollment(cls, name, mode):
        user = User.objects.create(username='%s@example.com' % name, password=cls.password)
        Profile.objects.create(user=user, name=name)
        enroll = CourseEnrollment.objects.create(course_id=cls.course_id, user=user, mode=mode)
        if mode == 'masters':
            ProgramCourseEnrollment.objects.create(course_enrollment=enroll)
        return user


class GradeImportExportViewTests(ViewTestsMixin, TestCase):

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read', return_value=Mock(percent=0.50))
    def test_get(self, mock_grade_factory):
        # Create a user who has an inactive enrollment in the course
        inactive_learner = self._make_enrollment('inactive_learner', 'masters')
        course_enrollment = CourseEnrollment.objects.get(course_id=self.course_id, user=inactive_learner)
        course_enrollment.is_active = False
        course_enrollment.save()

        self.client.login(username=self.staff.username, password=self.password)
        response = self.client.get(reverse('bulk_grades', args=[self.course_id]))
        self.assertEqual(response.status_code, 200)
        data = [row for row in response.streaming_content]
        self.assertEqual(len(data), 4)
        # The inactive user should not be included in the grade CSV export
        for row in data:
            self.assertNotIn(inactive_learner.username, str(row))

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read', return_value=Mock(percent=0.50))
    def test_get_filter_role(self, mock_grade_factory):
        role_to_exclude = 'ROLE_TO_EXCLUDE'
        CourseAccessRole.objects.create(
            user=self.audit_learner,
            course_id=self.course_id,
            role=role_to_exclude
        )
        self.client.login(username=self.staff.username, password=self.password)
        response = self.client.get(
            reverse('bulk_grades', args=[self.course_id]),
            {'excludedCourseRoles': [role_to_exclude]},
        )
        self.assertEqual(response.status_code, 200)
        data = [row for row in response.streaming_content]
        # audit_learner should not be included in the grade CSV export
        for row in data:
            self.assertNotIn(self.audit_learner.username, str(row))

    @patch.object(GradeCSVProcessor, 'load')
    @patch.object(GradeCSVProcessor, 'filtered_column_headers')
    def test_get_history_filters_columns(self, csv_load, filter_columns):
        # When I request the bulk grade history (error_id set in query)
        self.client.login(username=self.staff.username, password=self.password)
        response = self.client.get(f'{reverse("bulk_grades", args=[self.course_id])}?error_id=1')

        # Assert that column filtering is called for the history report...
        self.assertEqual(response.status_code, 200)
        filter_columns.assert_called()

    def test_post(self):
        csv_content = 'user_id,username,course_id,track,cohort'
        csv_content += ',new_override-' + self.subsection_short_ids[0]
        csv_content += ',new_override-' + self.subsection_short_ids[1] + '\n'
        csv_content += ','.join([str(self.audit_learner.id), self.audit_learner.username, self.course_id, '', '', '1', ''])
        csv_content += '\n'
        csv_content += ','.join([str(self.verified_learner.id), self.verified_learner.username, self.course_id, '', '', '', '1'])
        csv_content += '\n'
        csv_content += ','.join([str(self.masters_learner.id), self.masters_learner.username, self.course_id, '', '', '1', '1'])
        csv_content += '\n'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')

        self.client.login(username=self.staff.username, password=self.password)
        response = self.client.post(
            reverse('bulk_grades', args=[self.course_id]),
            {'csv': csv_file},
        )
        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(
            response.json(),
            {
                'saved': 3,
                'error_messages': [],
                'can_commit': False,
                'error_rows': [],
                'waiting': False,
                'processed': 3,
                'saved_error_id': None,
                'percentage': '100.0%',
                'total': 3,
                'result_id': None
            }
        )

    def test_post_error(self):
        # Given bad CSV content
        csv_content = 'bad'
        csv_file = SimpleUploadedFile('test_file.csv', csv_content.encode('utf8'), content_type='text/csv')

        # When I post the file
        self.client.login(username=self.staff.username, password=self.password)
        response = self.client.post(
            reverse('bulk_grades', args=[self.course_id]),
            {'csv': csv_file},
        )

        # Then I get an error response back
        self.assertEqual(response.status_code, 200)
        self.assertDictEqual(
            response.json(),
            {
                'saved': 0,
                'error_messages': ['Missing column: user_id (on line 1)'],
                'can_commit': False,
                'error_rows': [],
                'waiting': False,
                'processed': 0,
                'saved_error_id': None,
                'percentage': '0.0%',
                'total': 0,
                'result_id': None
            }
        )


class GradeOperationHistoryViewTests(ViewTestsMixin, TestCase):

    @patch.object(GradeCSVProcessor, 'get_committed_history')
    def test_get(self, mock_get_history):
        mocked_value = 'This is the history of overrrides'
        mock_get_history.return_value = mocked_value
        response = self.client.get(reverse('bulk_grades.history', args=[self.course_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mocked_value)


class InterventionsExportViewTests(ViewTestsMixin, TestCase):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.masters_learner_2 = cls._make_enrollment('masters2', 'masters')
        cls.learner_api_client_patcher = patch('bulk_grades.api.LearnerAPIClient')
        cls.mocked_learner_api_client = cls.learner_api_client_patcher.start()
        user_data = {'videos_overall': 12, 'videos_last_week': 0, 'problems_overall': 10,
                     'problems_last_week': 5, 'correct_problems_overall': 66, 'correct_problems_last_week': 44,
                     'problems_attempts_overall': 233, 'problems_attempts_last_week': 221,
                     'correct_problems_attempts_overall': 101, 'correct_problems_attempts_last_week': 99,
                     'forum_posts_overall': 2, 'forum_posts_last_week': 0, 'date_last_active': 2}
        cls.data = {
                'audit@example.com': user_data,
                'masters@example.com': user_data,
                'masters2@example.com': user_data,
        }
        cls.mocked_learner_api_client.return_value.courses.return_value.intervention.return_value.get.return_value = \
            cls.data

    @patch('lms.djangoapps.grades.api.CourseGradeFactory.read', return_value=Mock(percent=0.50))
    def test_get(self, mock_grade_factory):
        self.client.login(username=self.staff.username, password=self.password)
        response = self.client.get(reverse('interventions', args=[self.course_id]))
        self.assertEqual(response.status_code, 200)
        data = [row for row in response.streaming_content]
        self.assertEqual(len(data), 3)
