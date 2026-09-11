from .base import *  # noqa

DEBUG = False

# ── Security ──────────────────────────────────────────────────────────────────
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# The refresh cookie must never travel over plain HTTP in production.
REFRESH_COOKIE_SECURE = True

# Host-native nginx is the only proxy in front of the container, and it
# sets X-Forwarded-For with $proxy_add_x_forwarded_for. Anything further
# left in that header was written by the caller.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=1)
SECURE_REDIRECT_EXEMPT = [r"^api/health/"]
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "SAMEORIGIN"

# ── Static files ──────────────────────────────────────────────────────────────
# WhiteNoise with content-hashed filenames plus gzip/brotli variants, so nginx
# can serve /static/ with a far-future cache header. The image runs collectstatic
# at build time, so the manifest ships inside it.
#
# Must be set via STORAGES: STATICFILES_STORAGE was removed in Django 5.1 and is
# silently ignored if used. See the note in base.py.
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ── Database — persistent connections (1 hour TTL) ────────────────────────────
DATABASES["default"]["CONN_MAX_AGE"] = 3600  # noqa: F821

# ── Email ─────────────────────────────────────────────────────────────────────
# ── Transactional email ───────────────────────────────────────────────────
#
# Account verification, password resets, invitations and security notices go
# out through an EXTERNAL provider over SMTP. They must never route through
# MateMail's own Mail Engine: a platform whose password reset stops working
# when its mail system is down is a platform nobody can recover an account on.
#
# EMAIL_HOST has no default on purpose. It previously defaulted to "localhost",
# where nothing listens — a deployment that forgot the variable discarded every
# message silently, and if anything ever did listen on that port it would be
# the Mail Engine. An empty value is detected by
# apps.accounts.mailer.transactional_email_configured() and logged loudly
# instead of pretending to deliver.
#
# Credentials come from the environment. Nothing provider-specific is compiled
# in, so switching provider is a change of environment variables.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)

# A dedicated transactional sender on its own subdomain, so application mail
# has an authentication story independent of customer mail and a reputation
# problem on one cannot sink the other.
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="MateMail <noreply@mail.matemail.online>"
)
SERVER_EMAIL = env("SERVER_EMAIL", default=DEFAULT_FROM_EMAIL)

# ── Logging — structured output to stdout (captured by Docker / log aggregator) ─
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "django.utils.log.ServerFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
