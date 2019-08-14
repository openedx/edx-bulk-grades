""""
Pluggable Django App settings for test
"""
from __future__ import absolute_import, unicode_literals


def plugin_settings(settings):
    """"Injects local settings into django settings"""
    settings.ANALYTICS_API_CLIENT = {
        'url': 'mock',
        'token': 'edx'
    }
