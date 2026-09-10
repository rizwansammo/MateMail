from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = list({*env("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1").split(","), "localhost", "127.0.0.1"})

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_celery_beat",
    "django_celery_results",
]

LOCAL_APPS = [
    "apps.health",
    "apps.accounts",
    "apps.tenants",
    "apps.domains",
    "apps.mailboxes",
    "apps.dnshealth",
    "apps.aliases",
    "apps.forwarding",
    "apps.mailqueue",
    "apps.logs",
    "apps.quarantine",
    "apps.backups",
    "apps.billing",
    "apps.teams",
    "apps.platform_admin",
    "apps.webmail",
    "apps.mail_engine",
    "apps.smtp_policy",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.tenants.middleware.TenantMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://matemail:matemail@localhost:5432/matemail")
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# STORAGES, not STATICFILES_STORAGE.
#
# STATICFILES_STORAGE was deprecated in Django 4.2 and REMOVED in 5.1. It is no
# longer a Django setting at all, so the previous
# STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
# was silently inert: Django fell back to plain StaticFilesStorage and
# WhiteNoise's compression and content-hashing never ran. Verified against
# Django 5.1.4 — django.conf.global_settings has no STATICFILES_STORAGE.
#
# Plain storage here; prod.py swaps in WhiteNoise's manifest backend. Manifest
# storage refuses to resolve a file that is not in staticfiles.json, so having
# it active by default would make `manage.py test` and local development depend
# on collectstatic having been run first.
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Redis
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

# Cache — Redis-backed. Used for short-lived security state that must not live in
# the database or in a client-held token (e.g. the 2FA login challenge).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "matemail",
    }
}

# Celery
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = "django-db"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    "dns-check-all-domains": {
        "task": "dnshealth.check_all_active_domains_dns",
        "schedule": crontab(minute="*/15"),
    },
    "expire-trials": {
        "task": "billing.expire_trials",
        "schedule": crontab(hour="3", minute="0"),  # daily at 03:00 UTC
    },
}

# DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "apps.teams.authentication.APIKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "120/min",
        "auth": "5/min",
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# JWT
from datetime import timedelta
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("DJANGO_SECRET_KEY"),
}

# CORS
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS", default="http://localhost:3000").split(",")
CORS_ALLOW_CREDENTIALS = True

# Mail engine integration
MAIL_ENGINE_API_URL = env("MAIL_ENGINE_API_URL", default="http://localhost:8080")
MAIL_ENGINE_API_KEY = env("MAIL_ENGINE_API_KEY", default="")
# "stub" (default) = in-memory adapter, no engine needed (local dev + CI)
# "mailcow" = the real MateMail Mail Engine (Postfix/Dovecot/Rspamd via mailcow)
MAIL_ENGINE_ADAPTER = env("MAIL_ENGINE_ADAPTER", default="stub")

# MateMail platform settings
MAIL_DOMAIN = env("MAIL_DOMAIN", default="matemail.online")
MAIL_HOSTNAME = env("MAIL_HOSTNAME", default="mx.matemail.online")
DKIM_SELECTOR = env("DKIM_SELECTOR", default="mm1")

# Frontend URLs
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000")
APP_BASE_URL = env("APP_BASE_URL", default="http://localhost:3000")
WEBMAIL_BASE_URL = env("WEBMAIL_BASE_URL", default="http://localhost:3000")

# Internal API secret — used by the Mail Engine policy bridge and the webmail
# front end to call /api/internal/. nginx denies that prefix at the edge.
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(40))"
INTERNAL_API_SECRET = env("INTERNAL_API_SECRET", default="")
