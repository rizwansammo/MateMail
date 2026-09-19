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
    "apps.security",
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
    # Must follow TenantMiddleware: that is where an API key is resolved.
    "apps.security.middleware.APIKeyScopeMiddleware",
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

# Password hashing.
#
# Argon2 first, which makes it the *preferred* hasher: new and changed
# passwords use it, and Django re-hashes an account on its next successful
# login if the stored hash uses anything further down this list. That upgrade
# is why the older hashers stay — removing PBKDF2 would not migrate existing
# accounts, it would lock them out, since a password hash cannot be converted
# without the password itself.
#
# The list is Django's own default order with Argon2 moved to the front. Its
# parameters are argon2-cffi's defaults, which Django tracks; they are not
# tuned here, because a number picked without measuring this server's CPU and
# memory is not better than the maintained default.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

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
    # Ownership is proved once at onboarding; this is what notices when the
    # proof later disappears — a domain that has changed hands. Daily, because
    # the signal is sustained absence over about a week, not a single miss.
    "reverify-domain-ownership": {
        "task": "domains.reverify_ownership",
        "schedule": crontab(hour="4", minute="30"),  # daily at 04:30 UTC
    },
    "expire-trials": {
        "task": "billing.expire_trials",
        "schedule": crontab(hour="3", minute="0"),  # daily at 03:00 UTC
    },
}

# DRF
REST_FRAMEWORK = {
    # Order matters. simplejwt's JWTAuthentication accepts any "Bearer ..."
    # header and *raises* InvalidToken when the value is not a JWT — it does
    # not return None and pass to the next authenticator. With it first, every
    # `mm_` API key was rejected with 401 before APIKeyAuthentication ever ran,
    # so API keys had never worked at all. APIKeyAuthentication returns None
    # for anything that is not an `mm_` token, so JWTs still reach simplejwt.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.teams.authentication.APIKeyAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # The stock classes key on DRF's get_ident, which trusts X-Forwarded-For
    # wholesale unless NUM_PROXIES is set — an attacker varying the header
    # defeats every per-IP limit. These subclasses key on the trusted client
    # address and exempt the health and internal prefixes. See
    # apps.security.throttling.
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.security.throttling.MateMailAnonThrottle",
        "apps.security.throttling.MateMailUserThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        # 60/min per authenticated user, per the P3 rate-limit plan. The
        # previous 120/min predates that plan.
        "user": "60/min",
        "auth": "5/min",
        # Authenticated auth actions: resend verification, 2FA enrol, 2FA
        # disable. Previously unlimited — see AuthenticatedActionThrottle.
        "auth_action": "10/min",
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

# Mail engine integration.
#
# The URL deliberately has NO default. It used to default to
# "http://localhost:8080", which is wrong in every environment that matters:
# MateMail runs in a container, so loopback is the container itself, never the
# engine. Worse, the plausible-looking default masked the deploy check meant to
# catch a missing URL — a production deployment that forgot to set it would pass
# `check --deploy` and then fail on the first customer action.
#
# Empty means "not configured", which is exactly what mail_engine.E001 reports.
MAIL_ENGINE_API_URL = env("MAIL_ENGINE_API_URL", default="")
MAIL_ENGINE_API_KEY = env("MAIL_ENGINE_API_KEY", default="")
# "stub" (default) = in-memory adapter, no engine needed (local dev + CI)
# "mailcow" = the real MateMail Mail Engine (Postfix/Dovecot/Rspamd via mailcow)
MAIL_ENGINE_ADAPTER = env("MAIL_ENGINE_ADAPTER", default="stub")

# ── Native Engine (NE5) ──────────────────────────────────────────────────────
#
# Where the Native Engine's private API lives, and the credential for it. Only
# consulted when MAIL_ENGINE_ADAPTER is "native"; `manage.py check --deploy`
# refuses that combination if either is missing, so a half-configured switch
# cannot reach production.
#
# The URL is plain HTTP on purpose: this is an INTERNAL Docker network that
# publishes no ports, and the engine has no certificate until NE0.9's
# distribution model exists. It is not the public Internet and must not be
# confused with MAIL_ENGINE_API_URL, which reaches mailcow over TLS.
NATIVE_ENGINE_API_URL = env("NATIVE_ENGINE_API_URL", default="")
NATIVE_ENGINE_API_SECRET = env("NATIVE_ENGINE_API_SECRET", default="")

# MateMail platform settings
MAIL_DOMAIN = env("MAIL_DOMAIN", default="matemail.online")
MAIL_HOSTNAME = env("MAIL_HOSTNAME", default="mx.matemail.online")
DKIM_SELECTOR = env("DKIM_SELECTOR", default="mm1")

# Frontend URLs
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000")
APP_BASE_URL = env("APP_BASE_URL", default="http://localhost:3000")
WEBMAIL_BASE_URL = env("WEBMAIL_BASE_URL", default="http://localhost:3000")

# Transactional application email, sent through MateMail's own Mail Engine
# (DEC-013). The default here keeps every environment from falling back to
# Django's global "webmaster@localhost", which names no product and would be a
# confusing From address in a console-backend dev message.
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="MateMail <noreply@mail.matemail.online>"
)

# ── The platform sender (P5) ─────────────────────────────────────────────────
#
# MateMail's own service identity. It authenticates to the Mail Engine exactly
# like a customer does, so the outbound policy bridge sees it on every
# verification email, password reset and invitation — and it has no Mailbox row,
# no Domain row and no tenant, because it is not a customer.
#
# Without an explicit allowance the bridge rejects it as "Sender mailbox not
# found", which would take down account recovery for every customer the moment
# the policy service is enforced in the submission restrictions. Listing it here
# is how MateMail says "this identity is ours" in one place both the mailer and
# the policy bridge read.
#
# It is an allowance, NOT a bypass. The platform sender is still held to
# sender == sasl_username, so a stolen credential cannot send as anyone else,
# and it has its own rate limit below.
PLATFORM_SENDER_ADDRESSES = env.list(
    "PLATFORM_SENDER_ADDRESSES", default=["noreply@mail.matemail.online"]
)

# The platform sender's own hourly cap. Deliberately finite: "we trust this
# identity" is not the same as "this identity may send without limit", and the
# realistic failure here is a credential leak or a retry loop, both of which a
# ceiling contains.
#
# Set to match the limit the Mail Engine already holds on the platform mailbox
# — measured as 60/hour against the live engine, not assumed. The two must
# agree, and MateMail's must not be the higher of the pair: a MateMail limit
# above the engine's would never bind, so a looping sender would meet the
# engine's hard rejection instead of MateMail's DEFER, and a retryable
# condition would present as a permanent failure on a password reset.
PLATFORM_SENDER_MAX_PER_HOUR = env.int("PLATFORM_SENDER_MAX_PER_HOUR", default=60)

# DKIM private-key encryption at rest (INTERIM — see DEC-007r).
#
# P4 moves DKIM key generation and storage into the Mail Engine and drops
# Domain.dkim_private_key entirely. Until then the existing column is
# encrypted rather than left as plaintext in every database backup.
#
# Deliberately NOT derived from DJANGO_SECRET_KEY: that key signs sessions and
# JWTs and will be rotated for unrelated reasons, and a rotation that made
# every DKIM key undecryptable would surface as customer mail failing
# authentication. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#
# Comma-separated to allow rotation: the first key encrypts, all are tried when
# decrypting.
DKIM_ENCRYPTION_KEYS = env("DKIM_ENCRYPTION_KEY", default="")

# Refresh-token cookie. See apps.accounts.cookies for the full rationale.
# Secure is off by default so development over plain HTTP works at all; prod.py
# turns it on and a deployment must never override it back.
REFRESH_COOKIE_SECURE = env.bool("REFRESH_COOKIE_SECURE", default=False)
REFRESH_COOKIE_SAMESITE = env("REFRESH_COOKIE_SAMESITE", default="Strict")

# Reverse proxies between the public internet and this application whose
# X-Forwarded-For entries may be trusted. Host-native nginx is one hop and
# appends the address it actually saw, so with a value of 1 the rightmost
# entry is the real client. 0 means no proxy: only REMOTE_ADDR is trusted.
# Setting this too high lets a caller forge their own address.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)

# How many workspaces one user may own. Free signup plus unlimited workspace
# creation is a trial-abuse and resource-exhaustion path, so this is capped and
# overridable per environment without a code change.
MAX_WORKSPACES_PER_USER = env.int("MAX_WORKSPACES_PER_USER", default=5)

# Internal API secret — used by the Mail Engine policy bridge and the webmail
# front end to call /api/internal/. nginx denies that prefix at the edge.
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(40))"
INTERNAL_API_SECRET = env("INTERNAL_API_SECRET", default="")
