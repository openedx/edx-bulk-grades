#!/usr/bin/env python
"""
Tests for the `edx-bulk-grades` models module.
"""

from courseware.models import StudentModule
from django.contrib.auth.models import User
from django.test import TestCase

from bulk_grades.models import ScoreOverrider


class TestScoreOverrider(TestCase):
    """
    Tests of the ScoreOverrider model.
    """

    def test_str(self):
        user = User.objects.create(username='test')
        module = StudentModule.objects.create(
            student=user,
            module_state_key='block-v1:text+t+1+type@course+block@course')
        overrider = ScoreOverrider.objects.create(user=user, module=module)
        assert str(overrider) == 'ScoreOverrider(block-v1:text+t+1+type@course+block@course, test)'
