# -*- coding: utf-8 -*-
"""
URLs for bulk_grades.
"""
from __future__ import absolute_import, unicode_literals

from django.conf import settings
from django.conf.urls import url

from . import views

urlpatterns = [
    url(
        r'^bulk_grades/course/{}/$'.format(settings.COURSE_ID_PATTERN),
        views.GradeImportExport.as_view(),
        name='bulk_grades'
    ),
    url(
        r'^bulk_grades/course/{}/history/$'.format(settings.COURSE_ID_PATTERN),
        views.GradeOperationHistoryView.as_view(),
        name='bulk_grades.history'
    ),
]
