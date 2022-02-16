"""
URLs for bulk_grades.
"""

from django.conf import settings
from django.urls import re_path

from . import views

urlpatterns = [
    re_path(
        fr'^bulk_grades/course/{settings.COURSE_ID_PATTERN}/$',
        views.GradeImportExport.as_view(),
        name='bulk_grades'
    ),
    re_path(
        fr'^bulk_grades/course/{settings.COURSE_ID_PATTERN}/history/$',
        views.GradeOperationHistoryView.as_view(),
        name='bulk_grades.history'
    ),
    re_path(
        fr'^bulk_grades/course/{settings.COURSE_ID_PATTERN}/intervention/$',
        views.InterventionsExport.as_view(),
        name='interventions'
    ),
]
