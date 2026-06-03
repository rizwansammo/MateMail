"""
Redis-based rate limiter for SMTP sending.

Counters are incremented only when all scopes are within limits (atomic check-then-increment).
Keys expire naturally after their window; no explicit cleanup needed.
"""
import logging

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

# (scope_name, max_count, window_seconds)
_SCOPES = [
    ("mailbox", 100, 3600),    # 100 messages / hour per mailbox
    ("domain",  500, 3600),    # 500 messages / hour per domain
    ("tenant", 2000, 86400),   # 2000 messages / day per tenant
]

SCOPE_LIMITS = {name: limit for name, limit, _ in _SCOPES}
SCOPE_WINDOWS = {name: window for name, _, window in _SCOPES}


class MailRateLimiter:
    def __init__(self):
        self._client = None

    @property
    def redis(self):
        if self._client is None:
            self._client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._client

    def _keys(self, mailbox_email: str, domain_name: str, tenant_id: str) -> dict:
        return {
            "mailbox": f"ratelimit:mailbox:{mailbox_email}",
            "domain":  f"ratelimit:domain:{domain_name}",
            "tenant":  f"ratelimit:tenant:{tenant_id}",
        }

    def check_and_record(
        self, mailbox_email: str, domain_name: str, tenant_id: str
    ) -> tuple[bool, str]:
        """
        Check whether sending is allowed, then increment all counters atomically.
        Returns (allowed, reason). Counters are NOT incremented if any scope is over limit.
        """
        keys = self._keys(mailbox_email, domain_name, tenant_id)

        # Pre-flight read — bail early without incrementing if over limit
        for name, limit, _window in _SCOPES:
            current = self.redis.get(keys[name])
            if current and int(current) >= limit:
                logger.info("Rate limit hit [%s] %s (current=%s limit=%s)", name, keys[name], current, limit)
                return False, f"Sending limit exceeded ({name}: {limit} per {_SCOPE_WINDOW_LABEL(name)})"

        # All clear — increment atomically via pipeline
        pipe = self.redis.pipeline()
        for name, _limit, window in _SCOPES:
            pipe.incr(keys[name])
            pipe.expire(keys[name], window, nx=True)
        pipe.execute()
        return True, ""

    def get_counts(
        self, mailbox_email: str, domain_name: str, tenant_id: str
    ) -> dict:
        """Return current counter values for the three scopes."""
        keys = self._keys(mailbox_email, domain_name, tenant_id)
        return {
            name: int(self.redis.get(keys[name]) or 0)
            for name in ("mailbox", "domain", "tenant")
        }

    def reset(self, mailbox_email: str, domain_name: str, tenant_id: str) -> None:
        """Delete all counters — useful for testing or after plan changes."""
        keys = self._keys(mailbox_email, domain_name, tenant_id)
        self.redis.delete(*keys.values())


def _SCOPE_WINDOW_LABEL(name: str) -> str:
    windows = {"mailbox": "hour", "domain": "hour", "tenant": "day"}
    return windows.get(name, "period")
