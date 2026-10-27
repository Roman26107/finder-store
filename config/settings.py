"""Настройки FINDER из переменных окружения и локального .env."""

import os
import sys
from pathlib import Path

from .env import load_env_file

BASE_DIR = Path(__file__).resolve().parent.parent
load_env_file(BASE_DIR / '.env')


# Для production задайте собственный DJANGO_SECRET_KEY и отключите DJANGO_DEBUG.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-change-this-key-before-production',
)

DEBUG = os.environ.get('DJANGO_DEBUG', 'true').lower() in ('1', 'true', 'yes', 'on')


def env_bool(name, default=False):
    return os.environ.get(name, 'true' if default else 'false').lower() in (
        '1', 'true', 'yes', 'on',
    )


def env_list(name, default=''):
    return [value.strip() for value in os.environ.get(name, default).split(',') if value.strip()]

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
        'database_errors': {
            'class': 'shop.logging_handlers.DatabaseErrorHandler',
            'level': 'ERROR',
        },
    },
    'root': {
        'handlers': ['console', 'database_errors'],
        'level': 'INFO',
    },
    'loggers': {
        'django.request': {
            'handlers': ['console', 'database_errors'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost')
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')

# Курсы обновляются отдельной management-командой, не внутри web-worker.
CBR_UPDATE_INTERVAL_SECONDS = int(os.environ.get('CBR_UPDATE_INTERVAL_SECONDS', '300'))

# JSON-объект ключей M2M API; секреты хранятся только в окружении.
AURENE_API_KEYS_JSON = os.environ.get('AURENE_API_KEYS_JSON', '')
TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', '').strip().lstrip('@')
TELEGRAM_CHANNEL_USERNAME = os.environ.get(
    'TELEGRAM_CHANNEL_USERNAME', ''
).strip().lstrip('@')
AURENE_API_RATE_LIMIT_PER_MINUTE = int(
    os.environ.get('AURENE_API_RATE_LIMIT_PER_MINUTE', '300')
)
AURENE_API_IP_RATE_LIMIT_PER_MINUTE = int(
    os.environ.get('AURENE_API_IP_RATE_LIMIT_PER_MINUTE', '600')
)
AURENE_API_REQUIRE_HTTPS = os.environ.get(
    'AURENE_API_REQUIRE_HTTPS', 'false' if DEBUG else 'true'
).lower() in ('1', 'true', 'yes', 'on')
if os.environ.get('DJANGO_TRUST_X_FORWARDED_PROTO', 'false').lower() in (
    '1', 'true', 'yes', 'on',
):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'shop',
    'telegram_bot.apps.TelegramBotConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'shop.middleware.SensitiveActionRateLimitMiddleware',
    'shop.middleware.AdminSecondFactorMiddleware',
    'shop.analytics.AnalyticsMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

TESTING = 'test' in sys.argv
ADMIN_2FA_MAX_AGE_SECONDS = int(os.environ.get('ADMIN_2FA_MAX_AGE_SECONDS', '28800'))
DJANGO_APPLICATION_RATE_LIMIT_ENABLED = env_bool(
    'DJANGO_APPLICATION_RATE_LIMIT_ENABLED', not DEBUG and not TESTING,
)
# Разрешается только когда приложение недоступно в обход Nginx, а Nginx всегда
# перезаписывает X-Real-IP. M2M API намеренно продолжает использовать REMOTE_ADDR.
DJANGO_TRUST_X_REAL_IP = env_bool('DJANGO_TRUST_X_REAL_IP', False)

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'shop.context_processors.cart',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


if os.environ.get('DATABASE_ENGINE', 'sqlite').lower() in ('postgres', 'postgresql'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_DB', 'finder'),
            'USER': os.environ.get('POSTGRES_USER', 'finder'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
            'HOST': os.environ.get('POSTGRES_HOST', 'db'),
            'PORT': os.environ.get('POSTGRES_PORT', '5432'),
            'CONN_MAX_AGE': int(os.environ.get('DATABASE_CONN_MAX_AGE', '60')),
            'OPTIONS': {'connect_timeout': int(os.environ.get('DATABASE_CONNECT_TIMEOUT', '10'))},
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': Path(os.environ.get('SQLITE_PATH', BASE_DIR / 'db.sqlite3')),
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


LANGUAGE_CODE = 'ru-ru'

TIME_ZONE = 'Europe/Moscow'

USE_I18N = True

USE_TZ = True


STATIC_URL = '/static/'
STATIC_ROOT = Path(os.environ.get('STATIC_ROOT', BASE_DIR / 'staticfiles'))
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.environ.get('MEDIA_ROOT', BASE_DIR / 'media'))

SESSION_COOKIE_SECURE = env_bool('DJANGO_SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = env_bool('DJANGO_CSRF_COOKIE_SECURE', not DEBUG)
SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', False)
SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
SECURE_HSTS_PRELOAD = env_bool('DJANGO_SECURE_HSTS_PRELOAD', False)
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
CSRF_FAILURE_VIEW = 'shop.error_views.csrf_failure'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'shop:login'
LOGIN_REDIRECT_URL = 'shop:account'

EMAIL_BACKEND = os.environ.get(
    'DJANGO_EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
DEFAULT_FROM_EMAIL = os.environ.get('DJANGO_DEFAULT_FROM_EMAIL', 'FINDER <no-reply@finder.local>')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '25'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'false').lower() in ('1', 'true', 'yes', 'on')
