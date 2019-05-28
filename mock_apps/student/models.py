# -*- coding: utf-8 -*-
"""
Database models for bulk_grades.
"""

from __future__ import absolute_import, unicode_literals

from django.contrib.auth import get_user_model
from django.db import models
from django.utils.encoding import python_2_unicode_compatible


@python_2_unicode_compatible
class CourseEnrollment(models.Model):
    """
    Represents a Student's Enrollment record for a single Course. You should
    generally not manipulate CourseEnrollment objects directly, but use the
    classmethods provided to enroll, unenroll, or check on the enrollment status
    of a given student.

    We're starting to consolidate course enrollment logic in this class, but
    more should be brought in (such as checking against CourseEnrollmentAllowed,
    checking course dates, user permissions, etc.) This logic is currently
    scattered across our views.

    .. no_pii:
    """

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)

    course_id = models.CharField(max_length=255, db_index=True)

    created = models.DateTimeField(auto_now_add=True, null=True, db_index=True)

    # If is_active is False, then the student is not considered to be enrolled
    # in the course (is_enrolled() will return False)
    is_active = models.BooleanField(default=True)

    # Represents the modes that are possible. We'll update this later with a
    # list of possible values.
    mode = models.CharField(default='audit', max_length=100)

    class Meta(object):
        unique_together = (('user', 'course'),)
        ordering = ('user', 'course')
