"""
slumber client for REST service consumption.
"""

import logging

import requests
from django.conf import settings
from slumber import API, serialize

log = logging.getLogger(__name__)


class TokenAuth(requests.auth.AuthBase):
    """A requests auth class for DRF-style token-based authentication."""

    def __init__(self, token):
        """
        Constructor.
        """
        self.token = token

    def __call__(self, r):
        """
        Configure headers.
        """
        r.headers['Authorization'] = f'Token {self.token}'
        return r


class TextSerializer(serialize.BaseSerializer):
    """
    Slumber API Serializer for text data, e.g. CSV.
    """

    key = 'text'
    content_types = ('text/csv', 'text/plain', )

    def unchanged(self, data):
        """Leaves the request/response data unchanged."""
        return data

    # Define the abstract methods from BaseSerializer
    dumps = loads = unchanged


# pylint: disable=missing-class-docstring
class LearnerAPIClient(API):

    def __init__(self, timeout=5, serializer_type='json'):
        """
        Constructor.
        """
        session = requests.session()
        session.timeout = timeout

        serializers = serialize.Serializer(
            default=serializer_type,
            serializers=[
                serialize.JsonSerializer(),
                serialize.YamlSerializer(),
                TextSerializer(),
            ]
        )
        log.info('base url: %s', settings.ANALYTICS_API_CLIENT.get('url'))
        super().__init__(
            settings.ANALYTICS_API_CLIENT.get('url'),
            session=session,
            auth=TokenAuth(settings.ANALYTICS_API_CLIENT.get('token')),
            serializer=serializers,
        )
