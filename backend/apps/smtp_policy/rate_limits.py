"""
Outbound sending limits, enforced by MateMail at submission time.

Two things changed in P5.

**The limits come from the plan.** They were three literals here — 100/hour per
mailbox, 500/hour per domain, 2000/day per tenant — identical for every
workspace and invisible to the product. A limit nobody can see or change is not
a policy, and the same numbers applied to a free beta workspace and a future
enterprise plan.

**The check is genuinely atomic.** The previous implementation read every
counter, decided, and then incremented in a separate round-trip, while its
docstring claimed "atomic check-then-increment". It was not: two concurrent
submissions both read a count below the limit, both decided they were fine, and
both incremented. Under the concurrency that matters — a compromised account
sending as fast as it can — the limit leaked reliably. The check and the
increment now happen inside one Redis script, which Redis runs to completion
without interleaving.

This is MateMail's limiter, consulted by the policy bridge. It is deliberately
separate from the per-mailbox limit the engine itself enforces: this one can be
reasoned about in product terms and changed with a plan, while the engine's
holds even for a client that somehow reaches submission without us.
"""
import logging

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

#: Fallback used only when a workspace has no resolvable plan. Deliberately
#: tighter than any real plan: no plan is not a licence to send.
FALLBACK_MAILBOX_PER_HOUR = 20
FALLBACK_TENANT_PER_DAY = 100

_HOUR = 3600
_DAY = 86400

#: Check every scope, and only if ALL pass, increment every scope.
#:
#: The whole point is that this runs as one indivisible unit. Redis executes a
#: script to completion before serving another client, so no second submission
#: can observe the counters between the check and the increment.
#:
#: Returns 0 when allowed, or the 1-based index of the first scope that was
#: already at its limit — so the caller can name the scope without a second
#: round-trip.
_CHECK_AND_INCREMENT = """
local n = #KEYS
for i = 1, n do
  local limit = tonumber(ARGV[(i - 1) * 2 + 1])
  if limit > 0 then
    local current = tonumber(redis.call('GET', KEYS[i]) or '0')
    if current >= limit then
      return i
    end
  end
end
for i = 1, n do
  local ttl = tonumber(ARGV[(i - 1) * 2 + 2])
  local value = redis.call('INCR', KEYS[i])
  if value == 1 then
    redis.call('EXPIRE', KEYS[i], ttl)
  end
end
return 0
"""


class MailRateLimiter:
    def __init__(self):
        self._client = None
        self._script = None

    @property
    def redis(self):
        if self._client is None:
            self._client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._client

    @property
    def script(self):
        if self._script is None:
            self._script = self.redis.register_script(_CHECK_AND_INCREMENT)
        return self._script

    # ── Limits ──────────────────────────────────────────────────────────────

    @staticmethod
    def limits_for(plan) -> dict:
        """
        The limits that apply to a workspace, as `{scope: (limit, window_s)}`.

        A workspace with no plan gets the fallback, not "unlimited". Absence of
        a plan is a configuration gap, and the safe reading of a gap is the
        tighter one.
        """
        if plan is None:
            return {
                "mailbox": (FALLBACK_MAILBOX_PER_HOUR, _HOUR),
                "tenant": (FALLBACK_TENANT_PER_DAY, _DAY),
            }
        return {
            "mailbox": (plan.max_messages_per_hour_per_mailbox, _HOUR),
            "tenant": (plan.max_messages_per_day_per_tenant, _DAY),
        }

    @staticmethod
    def _keys(mailbox_email: str, tenant_id: str) -> dict:
        return {
            "mailbox": f"ratelimit:mailbox:{mailbox_email}",
            "tenant": f"ratelimit:tenant:{tenant_id}",
        }

    # ── Enforcement ─────────────────────────────────────────────────────────

    def check_and_record(self, *, mailbox_email: str, tenant_id: str, plan) -> tuple[bool, str]:
        """
        Decide whether this submission may proceed, and count it if so.

        Returns `(allowed, reason)`. The reason is customer-safe: it names the
        limit that was hit and the window, which is what a sender needs in
        order to understand a deferral, and nothing else.

        Counters are NOT incremented when the answer is no — a blocked
        submission must not push the sender further past the limit and extend
        its own lockout.
        """
        limits = self.limits_for(plan)
        keys = self._keys(mailbox_email, tenant_id)
        scopes = ["mailbox", "tenant"]

        argv = []
        for scope in scopes:
            limit, window = limits[scope]
            argv.extend([limit, window])

        try:
            blocked_at = int(
                self.script(keys=[keys[s] for s in scopes], args=argv)
            )
        except redis.RedisError:
            # Fail CLOSED. If the limiter cannot be consulted, MateMail cannot
            # know whether this send is within policy, and an unmetered send
            # path is exactly what the limiter exists to prevent. The policy
            # bridge turns this into a DEFER, so legitimate mail is retried
            # rather than lost.
            logger.exception(
                "Rate limiter unavailable for %s — deferring the send", mailbox_email
            )
            return False, "Sending is temporarily unavailable. Please try again shortly."

        if blocked_at == 0:
            return True, ""

        scope = scopes[blocked_at - 1]
        limit, window = limits[scope]
        label = "hour" if window == _HOUR else "day"
        noun = "mailbox" if scope == "mailbox" else "workspace"
        logger.info(
            "Rate limit reached [%s] %s (limit=%s per %s)", scope, mailbox_email, limit, label
        )
        return False, (
            f"This {noun} has reached its limit of {limit} messages per {label}. "
            "Sending will resume automatically."
        )

    def check_and_record_platform(self, *, sender: str) -> tuple[bool, str]:
        """
        The platform sender's own limit — one scope, no plan, no tenant.

        MateMail's service identity is trusted to send, not trusted to send
        without bound. The realistic failures are a leaked credential and a
        retry loop, and a ceiling contains both while being far above any real
        volume of verification emails and password resets.

        Shares the same Lua script, so it is atomic for the same reason.
        """
        limit = int(getattr(settings, "PLATFORM_SENDER_MAX_PER_HOUR", 500))
        key = f"ratelimit:platform:{sender}"

        try:
            blocked_at = int(self.script(keys=[key], args=[limit, _HOUR]))
        except redis.RedisError:
            # Fail closed, as for a customer. The bridge turns this into a
            # DEFER, so a verification email is retried rather than lost.
            logger.exception("Rate limiter unavailable for platform sender %s", sender)
            return False, "Sending is temporarily unavailable. Please try again shortly."

        if blocked_at == 0:
            return True, ""

        # Worth an error rather than an info: the platform sender hitting its
        # ceiling means either a loop or a compromised credential, and both
        # need a human.
        logger.error(
            "Platform sender %s reached its hourly limit of %s messages", sender, limit
        )
        return False, "Platform sending limit reached."

    def get_counts(self, *, mailbox_email: str, tenant_id: str) -> dict:
        keys = self._keys(mailbox_email, tenant_id)
        return {
            scope: int(self.redis.get(key) or 0)
            for scope, key in keys.items()
        }

    def status_for(self, *, mailbox_email: str, tenant_id: str, plan) -> dict:
        """Current usage against the applicable limits, for operator tooling."""
        counts = self.get_counts(mailbox_email=mailbox_email, tenant_id=tenant_id)
        limits = self.limits_for(plan)
        return {
            scope: {
                "current": counts[scope],
                "limit": limits[scope][0],
                "window_seconds": limits[scope][1],
            }
            for scope in counts
        }

    def reset(self, *, mailbox_email: str, tenant_id: str) -> None:
        """Clear counters — used by tests and after a deliberate policy change."""
        keys = self._keys(mailbox_email, tenant_id)
        self.redis.delete(*keys.values())
