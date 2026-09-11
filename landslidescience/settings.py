import datetime
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'insecure-dev-key-do-not-use-in-prod')
DEBUG = os.environ.get('DJANGO_DEBUG', 'false').lower() == 'true'

ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
    if h.strip()
]

# Self-hosted Umami, reached over the Docker network only — the browser never
# talks to it directly (see landslidescience/analytics.py). UMAMI_WEBSITE_ID
# empty = analytics off: the templates then emit no tracker tag at all, which
# is what a fresh clone or a developer without the stack running should get.
UMAMI_INTERNAL_URL = os.environ.get('UMAMI_INTERNAL_URL', 'http://umami:3000')
UMAMI_WEBSITE_ID = os.environ.get('UMAMI_WEBSITE_ID', '')
# A second Umami site, for signed-in collaborators, so their sessions never
# land in the public audience numbers. Empty = collaborators aren't counted.
UMAMI_TEAM_WEBSITE_ID = os.environ.get('UMAMI_TEAM_WEBSITE_ID', '')
# Where a human goes to READ the analytics (the Umami dashboard). Separate
# from UMAMI_INTERNAL_URL, which is the container address the beacon uses.
# Empty = no link is shown, which is correct until the subdomain exists.
UMAMI_PUBLIC_URL = os.environ.get('UMAMI_PUBLIC_URL', '')
# The Umami account the SSO bridge signs in as. Scoped `team-view-only`, so
# the token handed to a browser can read the analytics and change nothing.
UMAMI_BRIDGE_USER = os.environ.get('UMAMI_BRIDGE_USER', '')
UMAMI_BRIDGE_PASSWORD = os.environ.get('UMAMI_BRIDGE_PASSWORD', '')

# Trust X-Forwarded-Proto from Caddy so Django knows the request was HTTPS.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Origins allowed to POST to the admin (and any future forms).
CSRF_TRUSTED_ORIGINS = [
    f'https://{h}' for h in ALLOWED_HOSTS
    if h not in ('localhost', '127.0.0.1', '0.0.0.0')
]
if DEBUG:
    CSRF_TRUSTED_ORIGINS += ['http://localhost:8001', 'http://127.0.0.1:8001']

# Send cookies only over HTTPS in production.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# Rolling sessions: reset the expiry clock on every request so an actively-used
# editor session doesn't lapse mid-work. The window stays SESSION_COOKIE_AGE
# (Django default, 2 weeks) but counts from the last request, not from login.
SESSION_SAVE_EVERY_REQUEST = True

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'pages',
    'inventory',
    'files',
    'glaciers',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Where @login_required and friends send anonymous users. Points at the
# collaborator sign-in, NOT Django's /admin/login/ (which admits staff only).
LOGIN_URL = '/inventory/login/'
LOGIN_REDIRECT_URL = '/inventory/'
LOGOUT_REDIRECT_URL = '/inventory/'

ROOT_URLCONF = 'landslidescience.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'landslidescience' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'inventory.context_processors.user_roles',
                'landslidescience.analytics.website_id',
            ],
        },
    },
]

WSGI_APPLICATION = 'landslidescience.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'data' / 'db.sqlite3',
    }
}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Admin-uploaded hosted files (the `files` app). Lives under data/ so it's
# volume-mounted into the container in both dev and prod and gitignored.
# Served by files.views.serve at /files/<name> — there is no MEDIA_URL static
# route (no direct /media/ exposure).
MEDIA_ROOT = BASE_DIR / 'data' / 'media'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Tracy Arm 2025 embargo. ISO 8601 with offset; -04:00 = EDT (May is in DST).
TRACYARM_EMBARGO_LIFT = datetime.datetime.fromisoformat(
    os.environ.get('TRACYARM_EMBARGO_LIFT', '2026-05-06T08:00:00-04:00')
)
TRACYARM_YOUTUBE_URL = os.environ.get('TRACYARM_YOUTUBE_URL', '').strip()

