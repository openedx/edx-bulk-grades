"""
These settings are here to use during tests, because django requires them.

In a real-world use case, apps in this project are installed into other
Django applications, so these settings will not be used.
"""

from os.path import abspath, dirname, join

import djcelery


def root(*args):
    """
    Get the absolute path of the given path relative to the project root.
    """
    return join(abspath(dirname(__file__)), *args)


COURSE_KEY_PATTERN = r'(?P<course_key_string>[^/+]+(/|\+)[^/+]+(/|\+)[^/?]+)'
COURSE_ID_PATTERN = COURSE_KEY_PATTERN.replace('course_key_string', 'course_id')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': 'default.db',
        'USER': '',
        'PASSWORD': '',
        'HOST': '',
        'PORT': '',
    }
}

INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'bulk_grades',
    'super_csv',
    'courseware.apps.CoursewareConfig',
    'student',
    'djcelery',
)

LOCALE_PATHS = [
    root('bulk_grades', 'conf', 'locale'),
]

ROOT_URLCONF = 'bulk_grades.urls'

SECRET_KEY = 'insecure-secret-key'

ANALYTICS_API_BASE_URL = {
        'DEFAULT': 'mock',
        'mock': {},
    }
ANALYTICS_TOKEN = {
        'DEFAULT': 'edx'
    }

MIDDLEWARE = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
)
CELERY_ALWAYS_EAGER = True
CELERY_RESULT_BACKEND = 'djcelery.backends.cache:CacheBackend'
CELERY_EAGER_PROPAGATES_EXCEPTIONS = False
CELERY_BROKER_URL = BROKER_URL = 'memory://'
CELERY_BROKER_TRANSPORT = 'memory://'
CELERY_BROKER_HOSTNAME = 'localhost'

djcelery.setup_loader()
