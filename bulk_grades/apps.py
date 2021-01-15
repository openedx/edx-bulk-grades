"""
bulk_grades Django application initialization.
"""

from django.apps import AppConfig


class BulkGradesConfig(AppConfig):
    """
    Configuration for the bulk_grades Django application.
    """

    name = 'bulk_grades'
    plugin_app = {
        'url_config': {
            'lms.djangoapp': {
                'namespace': 'bulk_grades',
                'regex': '^api/',
                'relative_path': 'urls',
            },
        },
        'settings_config': {
            'lms.djangoapp': {
                'common': {'relative_path': 'settings.common'},
                'production': {'relative_path': 'settings.production'},
            },
        },
    }
