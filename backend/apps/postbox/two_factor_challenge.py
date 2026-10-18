"""Opaque, short-lived primary-password-proven PostBox MFA challenges.

A challenge never authenticates to the API; only its successful consumption
can create a PostBoxSession. Stored mailbox identity is a UUID, never a secret.
"""
import hashlib
import secrets

from django.core.cache import cache

PREFIX = "postbox:2fa:challenge:"
ATTEMPTS = "postbox:2fa:attempts:"
TTL_SECONDS = 300
MAX_ATTEMPTS = 5


def _key(raw):
    return PREFIX + hashlib.sha256(raw.encode("ascii")).hexdigest()


def _fail_key(raw):
    return ATTEMPTS + hashlib.sha256(raw.encode("ascii")).hexdigest()


def create_challenge(mailbox, *, remember=False):
    raw = secrets.token_urlsafe(36)
    cache.set(_key(raw), {
        "mailbox_id": str(mailbox.pk),
        "remember": bool(remember),
    }, timeout=TTL_SECONDS)
    return raw


def peek_challenge(raw):
    if not isinstance(raw, str) or not 32 <= len(raw) <= 128:
        return None
    return cache.get(_key(raw))


def consume_challenge(raw):
    if not raw or not isinstance(raw, str):
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
