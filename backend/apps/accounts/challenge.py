"""
Two-factor login challenge tokens.

A challenge token is issued after a *password* check succeeds but before the
second factor has been presented. It must therefore never be a credential that
ordinary API authentication will accept.

Design:
  - Opaque: 32 bytes of URL-safe entropy, not a JWT. It carries no claims, so
    DRF's JWTAuthentication cannot parse or accept it.
  - Server-side: the user/tenant binding lives in the Redis-backed cache, keyed
    by the SHA-256 of the token. The raw token is never stored.
  - Short-lived: 5-minute TTL, enforced by the cache.
  - Single-use: a successful verification claims the key via cache.delete(),
    which returns False if another request already claimed it.
  - Brute-force resistant: each failed code attempt is counted, and the
    challenge is destroyed after MAX_ATTEMPTS failures.
"""
import hashlib
import secrets

from django.core.cache import cache

CHALLENGE_TTL_SECONDS = 300
MAX_ATTEMPTS = 5

_KEY_PREFIX = "2fa:challenge:"
_ATTEMPT_PREFIX = "2fa:attempts:"


def _key(raw_token: str) -> str:
    return _KEY_PREFIX + hashlib.sha256(raw_token.encode()).hexdigest()


def _attempt_key(raw_token: str) -> str:
    return _ATTEMPT_PREFIX + hashlib.sha256(raw_token.encode()).hexdigest()


def _password_fingerprint(user) -> str:
    """
    Short digest of the stored password hash. Binding the challenge to this means
    a password change (e.g. a reset) instantly voids any in-flight challenge,
    without needing a reverse index from user to challenge keys.
    """
    return hashlib.sha256((user.password or "").encode()).hexdigest()[:32]


def create_challenge(user, tenant_id=None) -> str:
    """Issue an opaque single-use 2FA challenge token bound to `user`."""
    raw = secrets.token_urlsafe(32)
    cache.set(
        _key(raw),
        {
            "user_id": str(user.pk),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "pw": _password_fingerprint(user),
        },
        timeout=CHALLENGE_TTL_SECONDS,
    )
    return raw


def peek_challenge(raw_token: str) -> dict | None:
    """Return the challenge payload without consuming it, or None if absent/expired."""
    if not raw_token:
        return None
    return cache.get(_key(raw_token))


def challenge_matches_password(payload: dict, user) -> bool:
    """False if the account's password changed after the challenge was issued."""
    return bool(payload) and payload.get("pw") == _password_fingerprint(user)


def consume_challenge(raw_token: str) -> bool:
    """
    Atomically claim the challenge. Returns True exactly once per token;
    any later or concurrent claim returns False.
    """
    if not raw_token:
        return False
    cache.delete(_attempt_key(raw_token))
    return bool(cache.delete(_key(raw_token)))


def register_failed_attempt(raw_token: str) -> int:
    """
    Count a failed code attempt. Destroys the challenge once MAX_ATTEMPTS is
    reached. Returns the number of failures recorded so far.
    """
    if not raw_token:
        return 0
    key = _attempt_key(raw_token)
    try:
        attempts = cache.incr(key)
    except ValueError:
        # Key absent — seed it. TTL matches the challenge so it cannot outlive it.
        cache.set(key, 1, timeout=CHALLENGE_TTL_SECONDS)
        attempts = 1
    if attempts >= MAX_ATTEMPTS:
        cache.delete(_key(raw_token))
        cache.delete(key)
    return attempts


def discard_challenge(raw_token: str) -> None:
    """Drop a challenge without treating it as a successful use."""
    if not raw_token:
        return
    cache.delete(_key(raw_token))
    cache.delete(_attempt_key(raw_token))
