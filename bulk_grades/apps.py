# -*- coding: utf-8 -*-
"""
bulk_grades Django application initialization.
"""

from __future__ import absolute_import, unicode_literals

from django.apps import AppConfig


class BulkGradesConfig(AppConfig):
    """
    Configuration for the bulk_grades Django application.
    """

    name = 'bulk_grades'
    plugin_app = {
        'url_config': {
            'lms.djangoapp': {
                'namespace': u'bulk_grades',
                'regex': u'^api/',
                'relative_path': u'urls',
            },
        },
    }
