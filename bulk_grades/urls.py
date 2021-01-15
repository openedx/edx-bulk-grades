"""
URLs for bulk_grades.
"""

from django.conf import settings
from django.conf.urls import url

from . import views

urlpatterns = [
    url(
        fr'^bulk_grades/course/{settings.COURSE_ID_PATTERN}/$',
        views.GradeImportExport.as_view(),
        name='bulk_grades'
    ),
    url(
        fr'^bulk_grades/course/{settings.COURSE_ID_PATTERN}/history/$',
        views.GradeOperationHistoryView.as_view(),
        name='bulk_grades.history'
    ),
    url(
        fr'^bulk_grades/course/{settings.COURSE_ID_PATTERN}/intervention/$',
        views.InterventionsExport.as_view(),
        name='interventions'
    ),
]
