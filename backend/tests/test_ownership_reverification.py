"""
Periodic ownership re-verification, and platform-level mailbox suspension.

Both are P5 answers to the same shape of problem: a control that was asserted
once and then never enforced again.
"""
from unittest import mock

from django.test import TestCase

from apps.domains.models import Domain, DomainOwnership
from apps.domains.tasks import STALE_AFTER_FAILURES, reverify_domain_ownership
from apps.logs.models import LogEventType, MailLog
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.platform_admin.approval import ApprovalError, set_mailbox_suspended
from apps.tenants.models import TenantStatus
from tests.factories import (
    make_domain,
    make_mailbox,
    make_tenant,
    make_unverified_domain,
    make_user,
)

DNS_PATH = "apps.domains.verification.check_ownership_dns"


def _found():
    return (True, "", "")


def _missing():
    return (False, "The verification record could not be found.", "no TXT match")


class OwnershipReverificationTest(TestCase):
    """
    Ownership was proved once and never looked at again, so a domain that
    changed hands stayed claimed by whoever verified it first — while the new
    owner could never claim it, because the partial unique index gives the
    verified row to exactly one tenant.
    """

    def setUp(self):
        self.owner = make_user("owner@acme.example")
        self.tenant = make_tenant(self.owner, status=TenantStatus.ACTIVE)
        self.domain = make_domain(self.tenant, "acme.example")

    def _sweep(self, side_effect):
        with mock.patch(DNS_PATH, side_effect=side_effect):
            return reverify_domain_ownership()

    def test_a_still_valid_record_keeps_the_counter_at_zero(self):
        result = self._sweep(lambda d: _found())
        self.domain.refresh_from_db()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(self.domain.ownership_recheck_failures, 0)
        self.assertIsNotNone(self.domain.verification_last_checked_at)

    def test_a_missing_record_increments_the_counter(self):
        self._sweep(lambda d: _missing())
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.ownership_recheck_failures, 1)

    def test_the_counter_resets_on_any_success(self):
        """Only SUSTAINED absence should accumulate — DNS has bad afternoons."""
        for _ in range(3):
            self._sweep(lambda d: _missing())
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.ownership_recheck_failures, 3)

        self._sweep(lambda d: _found())
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.ownership_recheck_failures, 0)
        self.assertEqual(self.domain.verification_last_error, "")

    def test_sustained_absence_raises_a_flag_exactly_once(self):
        for _ in range(STALE_AFTER_FAILURES):
            result = self._sweep(lambda d: _missing())

        self.assertEqual(result["newly_stale"], 1)
        events = MailLog.objects.filter(
            tenant=self.tenant, event_type=LogEventType.DOMAIN_OWNERSHIP_STALE
        )
        self.assertEqual(events.count(), 1)
        self.assertEqual(
            events.first().metadata["consecutive_failures"], STALE_AFTER_FAILURES
        )

    def test_it_is_not_re_audited_on_every_later_run(self):
        """
        Otherwise the log fills with the same fact daily and the crossing
        itself becomes impossible to find.
        """
        for _ in range(STALE_AFTER_FAILURES + 5):
            self._sweep(lambda d: _missing())

        self.assertEqual(
            MailLog.objects.filter(
                event_type=LogEventType.DOMAIN_OWNERSHIP_STALE
            ).count(),
            1,
        )

    def test_nothing_is_deprovisioned_automatically(self):
        """
        The signal is the absence of a DNS record, which happens for many
        reasons that are not "this domain changed hands". Cutting off a
        legitimate customer would do far more damage, far more often.
        """
        for _ in range(STALE_AFTER_FAILURES + 3):
            self._sweep(lambda d: _missing())

        self.domain.refresh_from_db()
        self.assertEqual(self.domain.ownership_status, DomainOwnership.VERIFIED)
        self.assertTrue(Domain.objects.filter(pk=self.domain.pk).exists())

    def test_unverified_domains_are_not_swept(self):
        """They have nothing to re-verify, and would pollute the failure count."""
        make_unverified_domain(self.tenant, "not-yet.example")
        result = self._sweep(lambda d: _found())
        self.assertEqual(result["checked"], 1)

    def test_a_resolver_error_is_not_counted_as_a_failure(self):
        """
        Our own outage is not evidence that a customer lost their record.
        """
        with mock.patch(DNS_PATH, side_effect=OSError("resolver exploded")):
            result = reverify_domain_ownership()

        self.domain.refresh_from_db()
        self.assertEqual(self.domain.ownership_recheck_failures, 0)
        self.assertEqual(result["checked"], 1)

    def test_one_bad_domain_does_not_stop_the_sweep(self):
        other = make_domain(self.tenant, "second.example")
        calls = {"n": 0}

        def flaky(domain):
            calls["n"] += 1
            if domain.domain == self.domain.domain:
                raise OSError("resolver exploded")
            return _missing()

        with mock.patch(DNS_PATH, side_effect=flaky):
            result = reverify_domain_ownership()

        other.refresh_from_db()
        self.assertEqual(result["checked"], 2)
        self.assertEqual(other.ownership_recheck_failures, 1)


class PlatformMailboxSuspensionTest(TestCase):
    """
    The narrowest abuse response: stop one compromised account without
    touching a workspace that has done nothing wrong.
    """

    def setUp(self):
        self.admin = make_user("admin@matemail.online", is_staff=True)
        self.owner = make_user("owner@acme.example")
        self.tenant = make_tenant(self.owner, status=TenantStatus.ACTIVE)
        self.domain = make_domain(self.tenant, "acme.example")
        self.mailbox = make_mailbox(self.tenant, self.domain, local_part="alice")

    def test_suspending_sets_the_platform_only_state(self):
        mailbox = set_mailbox_suspended(
            self.mailbox.id, suspended=True, actor=self.admin, reason="compromised"
        )
        self.assertEqual(mailbox.status, MailboxStatus.SUSPENDED)

    def test_it_is_audited_with_the_actor(self):
        set_mailbox_suspended(
            self.mailbox.id, suspended=True, actor=self.admin, reason="compromised"
        )
        log = MailLog.objects.get(event_type=LogEventType.MAILBOX_SUSPENDED)
        self.assertEqual(log.source, self.admin.email)
        self.assertEqual(log.metadata["reason"], "compromised")

    def test_releasing_returns_the_mailbox_to_active(self):
        set_mailbox_suspended(self.mailbox.id, suspended=True, actor=self.admin)
        mailbox = set_mailbox_suspended(
            self.mailbox.id, suspended=False, actor=self.admin
        )
        self.assertEqual(mailbox.status, MailboxStatus.ACTIVE)

    def test_it_requires_an_attributable_human(self):
        with self.assertRaises(ApprovalError):
            set_mailbox_suspended(self.mailbox.id, suspended=True, actor=None)

    def test_a_redundant_change_is_refused(self):
        with self.assertRaises(ApprovalError):
            set_mailbox_suspended(self.mailbox.id, suspended=False, actor=self.admin)

    def test_the_engine_is_updated_for_a_provisioned_mailbox(self):
        Mailbox.objects.filter(pk=self.mailbox.pk).update(mail_engine_provisioned=True)
        adapter = mock.Mock()
        with mock.patch(
            "apps.mail_engine.factory.get_adapter", return_value=adapter
        ):
            set_mailbox_suspended(self.mailbox.id, suspended=True, actor=self.admin)

        adapter.set_mailbox_active.assert_called_once_with(self.mailbox.email, False)

    def test_an_engine_failure_rolls_back_the_whole_suspension(self):
        """
        A row saying `suspended` over a mailbox the engine will still accept
        submission from is exactly the false assurance this prevents.
        """
        from apps.mail_engine.errors import MailEngineError

        Mailbox.objects.filter(pk=self.mailbox.pk).update(mail_engine_provisioned=True)
        adapter = mock.Mock()
        adapter.set_mailbox_active.side_effect = MailEngineError("nope")

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            with self.assertRaises(ApprovalError):
                set_mailbox_suspended(
                    self.mailbox.id, suspended=True, actor=self.admin
                )

        self.mailbox.refresh_from_db()
        self.assertEqual(self.mailbox.status, MailboxStatus.ACTIVE)
        self.assertFalse(
            MailLog.objects.filter(event_type=LogEventType.MAILBOX_SUSPENDED).exists()
        )

    def test_a_tenant_cannot_lift_a_platform_suspension(self):
        """
        The whole point of having two states that both mean "not sending".
        """
        from tests.factories import auth_client

        set_mailbox_suspended(self.mailbox.id, suspended=True, actor=self.admin)
        api = auth_client(self.owner, self.tenant)
        res = api.patch(
            f"/api/mailboxes/{self.mailbox.id}/status/",
            {"status": "active"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

        self.mailbox.refresh_from_db()
        self.assertEqual(self.mailbox.status, MailboxStatus.SUSPENDED)

    def test_a_tenant_cannot_set_suspended_through_its_own_endpoint(self):
        from tests.factories import auth_client

        api = auth_client(self.owner, self.tenant)
        res = api.patch(
            f"/api/mailboxes/{self.mailbox.id}/status/",
            {"status": "suspended"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
