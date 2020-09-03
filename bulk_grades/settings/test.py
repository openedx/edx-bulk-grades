""""
Pluggable Django App settings for test
"""


def plugin_settings(settings):
    """"Injects local settings into django settings"""
    settings.ANALYTICS_API_CLIENT = {
        'url': 'mock',
        'token': 'edx'
    }


# CELERY
CELERY_ALWAYS_EAGER = True

results_dir = tempfile.TemporaryDirectory()
CELERY_RESULT_BACKEND = 'file://{}'.format(results_dir.name)