#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests for the `edx-bulk-grades` api module.
"""

from __future__ import absolute_import, unicode_literals
from django.contrib.auth.models import User
from django.test import TestCase
# from opaque_keys.edx.keys import UsageKey, CourseKey

from bulk_grades import api


class TestApi(TestCase):
    """
    Tests of the api functions.
    """

    def setUp(self):
        super(TestApi, self).setUp()
        self.learner = User.objects.create(username='student@example.com')
        self.staff = User.objects.create(username='staff@example.com')
        self.block_id = 'block-v1:testX+sg101+2019+type@test+block@85bb02dbd2c14ba5bc31a0264b140dda'
        self.course_id = 'course-v1:testX+sg101+2019'

    def test_set_score(self):
        api.set_score(self.block_id, self.learner.id, 11, 22, override_user_id=self.staff.id)
        score = api.get_score(self.block_id, self.learner.id)
        assert score['grade'] == .5
        assert score['who_last_graded'] == self.staff.id
        assert score['score'] == 11
        score = api.get_score(self.block_id, 11)
        assert score is None
