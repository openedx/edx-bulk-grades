"""
Common Pluggable Django App settings
"""


def plugin_settings(settings):
    """
    Injects local settings into django settings
    """
    env_tokens = getattr(settings, 'ENV_TOKENS', {})
    auth_tokens = getattr(settings, 'AUTH_TOKENS', {})
    if env_tokens.get('ANALYTICS_API_URL'):
        settings.ANALYTICS_API_CLIENT['url'] = env_tokens['ANALYTICS_API_URL']
    if auth_tokens.get('ANALYTICS_API_KEY'):
        settings.ANALYTICS_API_CLIENT['token'] = auth_tokens['ANALYTICS_API_KEY']
