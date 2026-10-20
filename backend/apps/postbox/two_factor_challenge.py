"""Opaque, short-lived primary-password-proven PostBox MFA challenges.

A challenge never authenticates to the API; only its successful consumption
can create a PostBoxSession. Stored mailbox identity is a UUID, never a secret.
"""
import hashlib
import secrets

from django.core.cache import cache

PREFIX = "postbox:2fa:challenge:"
ATTEMPTS = "postbox:2fa:attempts:"
CREDENTIAL_VERSION = "postbox:2fa:credential-version:"
TTL_SECONDS = 300
MAX_ATTEMPTS = 5


def _key(raw):
    return PREFIX + hashlib.sha256(raw.encode("ascii")).hexdigest()


def _fail_key(raw):
    return ATTEMPTS + hashlib.sha256(raw.encode("ascii")).hexdigest()


def _current_credential_version(mailbox_id):
    return cache.get(CREDENTIAL_VERSION + str(mailbox_id)) or "initial"


def invalidate_mailbox_challenges(mailbox):
    """Invalidate every password-proven partial challenge after password change.

    The independent version TTL outlives all issued 5-minute challenges.
    Refreshing MFA login always creates a challenge bound to the latest version.
    """
    cache.set(
        CREDENTIAL_VERSION + str(mailbox.pk), secrets.token_urlsafe(32),
        timeout=TTL_SECONDS * 4,
    )


def create_challenge(mailbox, *, remember=False):
    raw = secrets.token_urlsafe(36)
    cache.set(_key(raw), {
        "mailbox_id": str(mailbox.pk),
        "remember": bool(remember),
        "credential_version": _current_credential_version(mailbox.pk),
    }, timeout=TTL_SECONDS)
    return raw


def peek_challenge(raw):
    if not isinstance(raw, str) or not 32 <= len(raw) <= 128:
        return None
    payload = cache.get(_key(raw))
    if not payload or payload.get("credential_version") != _current_credential_version(
        payload.get("mailbox_id")
    ):
        return None
    return payload


def consume_challenge(raw):
    if peek_challenge(raw) is None:
        return False
    cache.delete(_fail_key(raw))
    return bool(cache.delete(_key(raw)))


def reject_attempt(raw):
    if not raw or not isinstance(raw, str):
        return
    key = _fail_key(raw)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=TTL_SECONDS)
        count = 1
    if count >= MAX_ATTEMPTS:
        cache.delete(_key(raw))
        cache.delete(key)
