"""
Outbound sending limits.

Run against a real Redis, because the two properties that matter — atomicity
and expiry — are properties of Redis, and a mock would assert only that the
code calls the functions it calls.

Every test uses its own key namespace via a unique mailbox address, and clears
up after itself, so a failed run cannot poison the next one.
"""
import unittest
import uuid

import redis
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from apps.billing.models import Plan
from apps.smtp_policy.rate_limits import (
    FALLBACK_MAILBOX_PER_HOUR,
    FALLBACK_TENANT_PER_DAY,
    MailRateLimiter,
)
from tests.factories import make_plan


def _redis_available() -> bool:
    try:
        redis.from_url(settings.REDIS_URL, socket_connect_timeout=2).ping()
        return True
    except Exception:
        return False


REDIS_UP = _redis_available()


class PlanDerivedLimitsTest(SimpleTestCase):
    """
    The numbers come from the plan, not from three literals in the limiter.

    Pure resolution, so no Redis and no database.
    """

    class FakePlan:
        max_messages_per_hour_per_mailbox = 250
        max_messages_per_day_per_tenant = 4000

    def test_limits_are_read_from_the_plan(self):
        limits = MailRateLimiter.limits_for(self.FakePlan())
        self.assertEqual(limits["mailbox"][0], 250)
        self.assertEqual(limits["tenant"][0], 4000)

    def test_the_windows_are_an_hour_and_a_day(self):
        limits = MailRateLimiter.limits_for(self.FakePlan())
        self.assertEqual(limits["mailbox"][1], 3600)
        self.assertEqual(limits["tenant"][1], 86400)

    def test_no_plan_gets_the_tighter_fallback_not_unlimited(self):
        """
        Absence of a plan is a configuration gap, and the safe reading of a gap
        is the tighter one. "No plan" must never mean "no limit".
        """
        limits = MailRateLimiter.limits_for(None)
        self.assertEqual(limits["mailbox"][0], FALLBACK_MAILBOX_PER_HOUR)
        self.assertEqual(limits["tenant"][0], FALLBACK_TENANT_PER_DAY)
        self.assertLess(limits["mailbox"][0], self.FakePlan.max_messages_per_hour_per_mailbox)


class SeededPlanLimitsTest(TestCase):
    """The shipped plans must all carry deliberate, finite limits."""

    def test_every_seeded_plan_has_finite_sending_limits(self):
        for tier in ("trial", "starter", "business", "infrastructure"):
            with self.subTest(tier=tier):
                plan = Plan.objects.filter(tier=tier).first()
                if plan is None:
                    self.skipTest(f"plan {tier} is not seeded in this database")
                self.assertGreater(plan.max_messages_per_hour_per_mailbox, 0)
                self.assertGreater(plan.max_messages_per_day_per_tenant, 0)
                self.assertGreater(plan.max_aliases, 0)

    def test_the_beta_plan_is_the_most_conservative(self):
        """
        A free, admin-approved workspace on shared sending reputation should
        have to ask for more rather than discover it already has it.
        """
        trial = Plan.objects.filter(tier="trial").first()
        business = Plan.objects.filter(tier="business").first()
        if not (trial and business):
            self.skipTest("plans are not seeded in this database")
        self.assertLessEqual(
            trial.max_messages_per_hour_per_mailbox,
            business.max_messages_per_hour_per_mailbox,
        )
        self.assertLessEqual(
            trial.max_messages_per_day_per_tenant,
            business.max_messages_per_day_per_tenant,
        )


class LimiterEnforcementTest(TestCase):
    """The limiter against a real Redis."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not REDIS_UP:
            raise unittest.SkipTest("Redis is not reachable")

    def setUp(self):
        self.limiter = MailRateLimiter()
        # Unique per test, so nothing leaks between tests or between runs.
        unique = uuid.uuid4().hex
        self.email = f"limit-{unique}@test.example"
        self.tenant_id = unique
        self.addCleanup(
            self.limiter.reset, mailbox_email=self.email, tenant_id=self.tenant_id
        )

    def _plan(self, *, per_hour, per_day):
        return make_plan(
            f"limit-test-{uuid.uuid4().hex[:8]}",
            max_messages_per_hour_per_mailbox=per_hour,
            max_messages_per_day_per_tenant=per_day,
        )

    def _send(self, plan):
        return self.limiter.check_and_record(
            mailbox_email=self.email, tenant_id=self.tenant_id, plan=plan
        )

    def test_sending_is_allowed_up_to_the_limit_and_refused_after(self):
        plan = self._plan(per_hour=3, per_day=100)
        for i in range(3):
            allowed, _ = self._send(plan)
            self.assertTrue(allowed, f"message {i + 1} should have been allowed")

        allowed, reason = self._send(plan)
        self.assertFalse(allowed)
        self.assertIn("3", reason)

    def test_a_blocked_send_does_not_count_against_the_sender(self):
        """
        Counting a refused submission would extend the sender's own lockout
        every time their client retried.
        """
        plan = self._plan(per_hour=2, per_day=100)
        self._send(plan)
        self._send(plan)
        for _ in range(5):
            self._send(plan)

        counts = self.limiter.get_counts(
            mailbox_email=self.email, tenant_id=self.tenant_id
        )
        self.assertEqual(counts["mailbox"], 2)

    def test_the_tenant_limit_holds_independently_of_the_mailbox_limit(self):
        plan = self._plan(per_hour=1000, per_day=2)
        self.assertTrue(self._send(plan)[0])
        self.assertTrue(self._send(plan)[0])

        allowed, reason = self._send(plan)
        self.assertFalse(allowed)
        self.assertIn("workspace", reason.lower())

    def test_no_scope_is_incremented_when_another_scope_blocks(self):
        """
        The check-then-increment must be all-or-nothing. If the mailbox is over
        its limit, the workspace's daily count must not move — otherwise one
        looping client burns the whole workspace's allowance.
        """
        plan = self._plan(per_hour=1, per_day=1000)
        self._send(plan)
        before = self.limiter.get_counts(
            mailbox_email=self.email, tenant_id=self.tenant_id
        )["tenant"]

        for _ in range(10):
            self._send(plan)

        after = self.limiter.get_counts(
            mailbox_email=self.email, tenant_id=self.tenant_id
        )["tenant"]
        self.assertEqual(before, after)

    def test_counters_are_given_an_expiry(self):
        """
        A counter with no TTL never resets, and the mailbox is banned forever.
        """
        plan = self._plan(per_hour=10, per_day=100)
        self._send(plan)
        client = self.limiter.redis
        self.assertGreater(client.ttl(f"ratelimit:mailbox:{self.email}"), 0)
        self.assertGreater(client.ttl(f"ratelimit:tenant:{self.tenant_id}"), 0)

    def test_a_zero_limit_means_unlimited_not_blocked(self):
        """
        Matches how the Mail Engine expresses "no limit". A plan configured
        with 0 must not silently stop a customer from sending anything at all.
        """
        plan = self._plan(per_hour=0, per_day=0)
        for _ in range(20):
            self.assertTrue(self._send(plan)[0])

    def test_status_reports_usage_against_the_plan(self):
        plan = self._plan(per_hour=9, per_day=99)
        self._send(plan)
        status = self.limiter.status_for(
            mailbox_email=self.email, tenant_id=self.tenant_id, plan=plan
        )
        self.assertEqual(status["mailbox"]["current"], 1)
        self.assertEqual(status["mailbox"]["limit"], 9)
        self.assertEqual(status["tenant"]["limit"], 99)

    def test_the_refusal_message_is_customer_safe(self):
        plan = self._plan(per_hour=1, per_day=100)
        self._send(plan)
        _, reason = self._send(plan)
        for word in ("redis", "lua", "script", "tenant_id", "traceback"):
            with self.subTest(word=word):
                self.assertNotIn(word, reason.lower())


class LimiterFailsClosedTest(TestCase):
    """
    If the limiter cannot be consulted, MateMail does not know whether a send
    is within policy — and an unmetered send path is exactly what the limiter
    exists to prevent.
    """

    def setUp(self):
        self.limiter = MailRateLimiter()

    def test_a_redis_outage_denies_rather_than_allows(self):
        class BrokenScript:
            def __call__(self, *a, **kw):
                raise redis.ConnectionError("redis is down")

        self.limiter._script = BrokenScript()
        allowed, reason = self.limiter.check_and_record(
            mailbox_email="anyone@test.example", tenant_id="t", plan=None
        )
        self.assertFalse(allowed)
        self.assertIn("temporarily", reason.lower())

    def test_the_platform_sender_also_fails_closed(self):
        class BrokenScript:
            def __call__(self, *a, **kw):
                raise redis.ConnectionError("redis is down")

        self.limiter._script = BrokenScript()
        allowed, _ = self.limiter.check_and_record_platform(
            sender="noreply@mail.matemail.online"
        )
        self.assertFalse(allowed)


class PlatformSenderLimitTest(TestCase):
    """MateMail's own identity is trusted to send, not to send without bound."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not REDIS_UP:
            raise unittest.SkipTest("Redis is not reachable")

    def setUp(self):
        self.limiter = MailRateLimiter()
        self.sender = f"noreply-{uuid.uuid4().hex}@mail.matemail.online"
        self.addCleanup(self.limiter.redis.delete, f"ratelimit:platform:{self.sender}")

    @override_settings(PLATFORM_SENDER_MAX_PER_HOUR=3)
    def test_the_platform_sender_has_a_finite_ceiling(self):
        for _ in range(3):
            self.assertTrue(self.limiter.check_and_record_platform(sender=self.sender)[0])
        allowed, _ = self.limiter.check_and_record_platform(sender=self.sender)
        self.assertFalse(allowed)

    @override_settings(PLATFORM_SENDER_MAX_PER_HOUR=500)
    def test_the_default_ceiling_is_well_above_real_transactional_volume(self):
        """It must never throttle account recovery in normal operation."""
        for _ in range(50):
            self.assertTrue(self.limiter.check_and_record_platform(sender=self.sender)[0])
