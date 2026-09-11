"""
The rate-limit primitive the abuse controls are built on.

Fixed windows aligned to the wall clock: the key carries the window it belongs
to, so `cache.add` + `cache.incr` are enough — both are atomic in Redis, which
matters because two gunicorn workers can process the same attacker's requests
at the same instant. A read-modify-write counter would lose increments exactly
when it is under attack.

The trade-off of a fixed window is the boundary: an attacker can spend a full
allowance at the end of one window and another at the start of the next. For
credential and signup abuse that is not meaningful — it doubles the budget over
twice the time, and the numbers here are chosen well below what an attack needs.
It buys exact `Retry-After` values and an implementation with no lost updates,
which a sliding log would not.

Identities are supplied by the caller and must already be normalised. A None or
empty identity is bucketed under a fixed "unknown" key rather than being
allowed through: an unidentifiable caller is the case an attacker will try to
create.
"""
import hashlib
import time
from dataclasses import dataclass

from django.core.cache import cache

_PREFIX = "rl:"
_UNKNOWN = "unknown"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    count: int
    limit: int
    #: Seconds until the current window ends. Suitable for `Retry-After`.
    retry_after: int


def _identity(value) -> str:
    if value is None:
        return _UNKNOWN
    text = str(value).strip().lower()
    if not text:
        return _UNKNOWN
    # Hash rather than embed: identities include email addresses, and cache
    # keys end up in logs, INFO output and Redis key dumps.
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _key(bucket: str, identity, window: int, window_start: int) -> str:
    return f"{_PREFIX}{bucket}:{_identity(identity)}:{window}:{window_start}"


def _window_bounds(window: int) -> tuple[int, int]:
    now = int(time.time())
    start = now - (now % window)
    return start, start + window - now


def check(bucket: str, identity, *, limit: int, window: int) -> Decision:
    """Report the current state without consuming an attempt."""
    start, remaining = _window_bounds(window)
    count = cache.get(_key(bucket, identity, window, start)) or 0
    return Decision(
        allowed=count < limit,
        count=count,
        limit=limit,
        retry_after=max(1, remaining),
    )


def hit(bucket: str, identity, *, limit: int, window: int) -> Decision:
    """
    Consume one attempt and report whether it was within the limit.

    `allowed` is False on the attempt that exceeds the limit and on every
    attempt after it, so callers can refuse the request that trips it.
    """
    start, remaining = _window_bounds(window)
    key = _key(bucket, identity, window, start)

    if cache.add(key, 1, timeout=window):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            # The key expired between add() and incr(). Re-seed; at worst this
            # forgives one attempt at a window boundary.
            cache.set(key, 1, timeout=window)
            count = 1

    return Decision(
        allowed=count <= limit,
        count=count,
        limit=limit,
        retry_after=max(1, remaining),
    )


def reset(bucket: str, identity, *, window: int) -> None:
    """
    Clear the current window for one identity.

    Used after a successful login: a legitimate user who mistypes a password
    twice should not carry those failures for the rest of the window.
    """
    start, _ = _window_bounds(window)
    cache.delete(_key(bucket, identity, window, start))


def claim_once(bucket: str, identity, *, ttl: int) -> bool:
    """
    Single-use claim. True exactly once per (bucket, identity) within `ttl`.

    `cache.add` is atomic, so two concurrent requests presenting the same value
    cannot both win. Used to stop an accepted TOTP code being replayed inside
    the timestep it remains valid for.
    """
    return bool(cache.add(f"{_PREFIX}once:{bucket}:{_identity(identity)}", 1, timeout=ttl))
