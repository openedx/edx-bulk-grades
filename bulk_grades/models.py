# -*- coding: utf-8 -*-
"""
Database models for bulk_grades.
"""

from __future__ import absolute_import, unicode_literals

from django.contrib.auth import get_user_model
from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from model_utils.models import TimeStampedModel


@python_2_unicode_compatible
class ScoreOverrider(TimeStampedModel):
    """
    Records who overrode a score, for bulk score assignments.

    .. no_pii:
    """

    module = models.ForeignKey('courseware.StudentModule', db_constraint=False, on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE)

    class Meta(object):
        """Django model meta."""

        app_label = "bulk_grades"

    def __str__(self):
        """Return string representation."""
        # pylint: disable=no-member
        return 'ScoreOverrider({}, {})'.format(self.module.module_state_key, self.user)
