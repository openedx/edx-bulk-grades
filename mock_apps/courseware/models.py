from django.contrib.auth import get_user_model
from django.db import models
from opaque_keys.edx.django.models import CourseKeyField, UsageKeyField


class StudentModule(models.Model):
    """
    Keeps student state for a particular module in a particular course.

    .. no_pii:
    """
    module_type = models.CharField(max_length=32, default='problem', db_index=True)

    # Key used to share state. This is the XBlock usage_id
    module_state_key = UsageKeyField(max_length=255, db_column='module_id')
    student = models.ForeignKey(get_user_model(), db_constraint=False, db_index=True, on_delete=models.CASCADE)

    course_id = CourseKeyField(max_length=255, db_index=True)

    class Meta:
        app_label = "courseware"
        unique_together = (('student', 'module_state_key', 'course_id'),)

    # Internal state of the object
    state = models.TextField(null=True, blank=True)

    # Grade, and are we done?
    grade = models.FloatField(null=True, blank=True, db_index=True)
    max_grade = models.FloatField(null=True, blank=True)
    done = models.CharField(max_length=8, default='na')

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True, db_index=True)
