""""
Pluggable Django App settings for test
"""


def plugin_settings(settings):
    """"Injects local settings into django settings"""
    settings.ANALYTICS_API_CLIENT = {
        'url': 'mock',
        'token': 'edx'
    }
