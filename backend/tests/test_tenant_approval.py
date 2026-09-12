"""
The approval gate, and the abuse controls that sit on top of it.

MateMail is free and admin-approved during the Private Beta, which makes
approval the single thing standing between an anonymous signup and a working
outbound mail server. These tests assert the gate actually holds, at every path
that can reach the Mail Engine, rather than only at the endpoint someone
remembered to guard.
"""
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.domains.models import Domain
from apps.logs.models import LogEventType, MailLog
from apps.platform_admin.approval import (
    ApprovalError,
    approve_tenant,
    reactivate_tenant,
    reject_tenant,
    set_outbound_enabled,
    suspend_tenant,
)
from apps.tenants.models import Tenant, TenantStatus
from apps.tenants.policy import (
    MailNotPermitted,
    assert_can_send_mail,
    assert_can_use_mail,
    is_temporary_denial,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_unapproved_tenant,
    make_user,
)


#: Signup is limited per IP by `apps.security.ratelimit`, which counts in the
#: Django cache rather than through a DRF throttle — so `disable_throttling`
#: does not reach it. Without a cache of its own, this class inherits whatever
#: budget earlier signup tests in the same run already spent, and fails with 429
#: depending on test order.
LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tenant-approval-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class SignupLeavesWorkspacePendingTest(TestCase):
    """
    A new workspace must not be able to provision mail on its own say-so.

    This is the property the whole beta rests on: anyone can sign up, and
    signing up grants nothing.
    """

    def setUp(self):
        disable_throttling(self)

    def test_signup_creates_a_pending_workspace(self):
        res = self.client.post(
            reverse("auth-signup"),
            {
                "email": "founder@newco.example",
                "password": "Test-Passphrase-42",
                "full_name": "A Founder",
                "workspace_name": "NewCo",
            },
            content_type="application/json",
        )
        self.assertIn(res.status_code, (200, 201))

        tenant = Tenant.objects.get(name="NewCo")
        self.assertEqual(tenant.status, TenantStatus.PENDING_APPROVAL)
        self.assertIsNone(tenant.approved_at)
        self.assertFalse(tenant.can_use_mail)
        self.assertFalse(tenant.can_send_mail)


class MailCapabilityPolicyTest(TestCase):
    """`apps.tenants.policy` — the one place the rule lives."""

    def setUp(self):
        self.user = make_user("owner@acme.example")

    def _tenant(self, status, *, approved, outbound_disabled=False):
        tenant = make_tenant(
            self.user,
            slug=f"acme-{status}-{approved}",
            status=status,
            approved=approved,
        )
        if outbound_disabled:
            tenant.outbound_disabled = True
            tenant.save(update_fields=["outbound_disabled"])
        return tenant

    def test_an_approved_active_workspace_may_use_and_send(self):
        tenant = self._tenant(TenantStatus.ACTIVE, approved=True)
        assert_can_use_mail(tenant)
        assert_can_send_mail(tenant)

    def test_every_non_mail_status_is_denied(self):
        for status in (
            TenantStatus.PENDING_APPROVAL,
            TenantStatus.REJECTED,
            TenantStatus.SUSPENDED,
            TenantStatus.CANCELLED,
            TenantStatus.PAST_DUE,
        ):
            with self.subTest(status=status):
                tenant = self._tenant(status, approved=True)
                with self.assertRaises(MailNotPermitted):
                    assert_can_use_mail(tenant)

    def test_a_mail_enabled_status_without_an_approval_is_still_denied(self):
        """
        The timestamp is the fact, not the status.

        Reachable whenever a status is set directly — a fixture, a shell, a
        future admin action that forgets to go through `approve_tenant`.
        """
        tenant = self._tenant(TenantStatus.ACTIVE, approved=False)
        with self.assertRaises(MailNotPermitted) as ctx:
            assert_can_use_mail(tenant)
        self.assertEqual(ctx.exception.reason_code, "not_approved")

    def test_an_unknown_status_fails_closed(self):
        tenant = self._tenant(TenantStatus.ACTIVE, approved=True)
        tenant.status = "some_status_invented_later"
        with self.assertRaises(MailNotPermitted) as ctx:
            assert_can_use_mail(tenant)
        self.assertEqual(ctx.exception.reason_code, "unknown_state")

    def test_a_missing_tenant_fails_closed(self):
        with self.assertRaises(MailNotPermitted):
            assert_can_use_mail(None)

    def test_outbound_disabled_blocks_sending_but_not_use(self):
        """The lighter abuse response: settings still work, sending does not."""
        tenant = self._tenant(
            TenantStatus.ACTIVE, approved=True, outbound_disabled=True
        )
        assert_can_use_mail(tenant)
        with self.assertRaises(MailNotPermitted) as ctx:
            assert_can_send_mail(tenant)
        self.assertEqual(ctx.exception.reason_code, "outbound_disabled")

    def test_refusal_messages_never_leak_internal_vocabulary(self):
        banned = ("status", "tenant", "engine", "mailcow", "traceback")
        for status in (
            TenantStatus.PENDING_APPROVAL,
            TenantStatus.REJECTED,
            TenantStatus.SUSPENDED,
            TenantStatus.CANCELLED,
            TenantStatus.PAST_DUE,
        ):
            tenant = self._tenant(status, approved=True)
            with self.assertRaises(MailNotPermitted) as ctx:
                assert_can_use_mail(tenant)
            message = ctx.exception.customer_message.lower()
            for word in banned:
                with self.subTest(status=status, word=word):
                    self.assertNotIn(word, message)


class BounceClassificationTest(TestCase):
    """
    Which refusals are permanent (5xx) and which are temporary (4xx).

    The distinction is mail delayed versus mail destroyed — see
    `apps.tenants.policy.TEMPORARY_DENIALS`.
    """

    def test_reversible_conditions_are_temporary(self):
        for code in ("pending_approval", "not_approved", "suspended", "past_due"):
            with self.subTest(code=code):
                self.assertTrue(is_temporary_denial(code))

    def test_settled_decisions_are_permanent(self):
        for code in ("rejected", "cancelled"):
            with self.subTest(code=code):
                self.assertFalse(is_temporary_denial(code))

    def test_not_knowing_is_temporary_not_permanent(self):
        """
        "I do not know" must never be expressed to a sender as "this address
        does not exist" — that destroys someone's mail over our own confusion.
        """
        self.assertTrue(is_temporary_denial("unknown_state"))
        self.assertTrue(is_temporary_denial("no_tenant"))
        self.assertTrue(is_temporary_denial("a_code_nobody_has_classified_yet"))

    def test_permission_is_not_a_denial(self):
        self.assertFalse(is_temporary_denial(""))


class ApprovalTransitionTest(TestCase):
    """The service functions: locks, actors, audit, and refusals."""

    def setUp(self):
        self.admin = make_user("admin@matemail.online", is_staff=True)
        self.owner = make_user("owner@acme.example")
        self.tenant = make_unapproved_tenant(self.owner)

    def test_approval_records_who_and_when(self):
        tenant = approve_tenant(
            self.tenant.id, actor=self.admin, reason="verified by phone"
        )

        self.assertEqual(tenant.status, TenantStatus.TRIAL)
        self.assertIsNotNone(tenant.approved_at)
        self.assertEqual(tenant.approved_by, self.admin)
        self.assertTrue(tenant.can_use_mail)

    def test_approval_is_audited(self):
        approve_tenant(self.tenant.id, actor=self.admin, reason="ok")
        log = MailLog.objects.get(
            tenant=self.tenant, event_type=LogEventType.TENANT_APPROVED
        )
        self.assertEqual(log.source, self.admin.email)
        self.assertEqual(log.metadata["from_status"], TenantStatus.PENDING_APPROVAL)

    def test_an_approval_requires_an_attributable_human(self):
        """An approval nobody signed is not an approval."""
        for actor in (None, mock.Mock(is_authenticated=False)):
            with self.subTest(actor=actor):
                with self.assertRaises(ApprovalError):
                    approve_tenant(self.tenant.id, actor=actor)
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.approved_at)

    def test_rejection_revokes_approval_rather_than_overriding_it(self):
        approve_tenant(self.tenant.id, actor=self.admin)
        tenant = reject_tenant(self.tenant.id, actor=self.admin, reason="bulk sender")

        self.assertEqual(tenant.status, TenantStatus.REJECTED)
        # Cleared, so no later status change can silently restore mail access.
        self.assertIsNone(tenant.approved_at)
        self.assertIsNone(tenant.approved_by)
        self.assertFalse(tenant.can_use_mail)

    def test_rejection_is_reversible(self):
        reject_tenant(self.tenant.id, actor=self.admin, reason="mistake")
        tenant = approve_tenant(self.tenant.id, actor=self.admin, reason="appealed")
        self.assertTrue(tenant.can_use_mail)

    def test_rejection_deletes_nothing(self):
        domain = make_domain(self.tenant, "acme.example")
        reject_tenant(self.tenant.id, actor=self.admin)
        self.assertTrue(Domain.objects.filter(pk=domain.pk).exists())

    def test_approving_a_cancelled_workspace_is_refused(self):
        self.tenant.status = TenantStatus.CANCELLED
        self.tenant.save(update_fields=["status"])
        with self.assertRaises(ApprovalError):
            approve_tenant(self.tenant.id, actor=self.admin)


class OutboundKillSwitchTest(TestCase):
    def setUp(self):
        self.admin = make_user("admin@matemail.online", is_staff=True)
        self.owner = make_user("owner@acme.example")
        self.tenant = make_tenant(self.owner, status=TenantStatus.ACTIVE)

    def test_disabling_outbound_leaves_everything_else_working(self):
        tenant = set_outbound_enabled(
            self.tenant.id, enabled=False, actor=self.admin, reason="spam reports"
        )
        self.assertTrue(tenant.outbound_disabled)
        self.assertIsNotNone(tenant.outbound_disabled_at)
        self.assertTrue(tenant.can_use_mail)
        self.assertFalse(tenant.can_send_mail)

    def test_re_enabling_clears_the_timestamp(self):
        set_outbound_enabled(self.tenant.id, enabled=False, actor=self.admin)
        tenant = set_outbound_enabled(self.tenant.id, enabled=True, actor=self.admin)
        self.assertFalse(tenant.outbound_disabled)
        self.assertIsNone(tenant.outbound_disabled_at)
        self.assertTrue(tenant.can_send_mail)

    def test_both_directions_are_audited(self):
        set_outbound_enabled(
            self.tenant.id, enabled=False, actor=self.admin, reason="abuse"
        )
        set_outbound_enabled(
            self.tenant.id, enabled=True, actor=self.admin, reason="cleared"
        )
        events = set(
            MailLog.objects.filter(tenant=self.tenant).values_list(
                "event_type", flat=True
            )
        )
        self.assertIn(LogEventType.TENANT_OUTBOUND_DISABLED, events)
        self.assertIn(LogEventType.TENANT_OUTBOUND_ENABLED, events)

    def test_a_redundant_change_is_refused_rather_than_silently_accepted(self):
        with self.assertRaises(ApprovalError):
            set_outbound_enabled(self.tenant.id, enabled=True, actor=self.admin)


class SuspensionReachesTheEngineTest(TestCase):
    """
    Suspension must be two mechanisms, not one database field.

    A suspended workspace whose domains are still active in the engine is
    stopped only by the policy bridge — one service, one config line and one
    restart away from not running.
    """

    def setUp(self):
        self.admin = make_user("admin@matemail.online", is_staff=True)
        self.owner = make_user("owner@acme.example")
        self.tenant = make_tenant(self.owner, status=TenantStatus.ACTIVE)

    def test_suspension_queues_engine_deactivation(self):
        with mock.patch(
            "apps.mail_engine.tasks.apply_tenant_suspension_task.delay"
        ) as delay:
            tenant, queued = suspend_tenant(
                self.tenant.id, actor=self.admin, reason="abuse"
            )

        self.assertEqual(tenant.status, TenantStatus.SUSPENDED)
        self.assertTrue(queued)
        delay.assert_called_once_with(str(self.tenant.id), False)

    def test_a_broker_failure_is_reported_not_swallowed(self):
        """
        An operator who believes a suspension took effect stops watching an
        incident that is still running.
        """
        with mock.patch(
            "apps.mail_engine.tasks.apply_tenant_suspension_task.delay",
            side_effect=RuntimeError("broker down"),
        ):
            tenant, queued = suspend_tenant(self.tenant.id, actor=self.admin)

        self.assertEqual(tenant.status, TenantStatus.SUSPENDED)
        self.assertFalse(queued)

    def test_suspension_keeps_the_approval(self):
        """A billing suspension must not cost the customer a fresh approval."""
        with mock.patch("apps.mail_engine.tasks.apply_tenant_suspension_task.delay"):
            tenant, _ = suspend_tenant(self.tenant.id, actor=self.admin)
        self.assertIsNotNone(tenant.approved_at)
        # Denied on status, not on approval.
        self.assertFalse(tenant.can_use_mail)

    def test_reactivation_restores_the_engine_too(self):
        with mock.patch("apps.mail_engine.tasks.apply_tenant_suspension_task.delay"):
            suspend_tenant(self.tenant.id, actor=self.admin)
        with mock.patch(
            "apps.mail_engine.tasks.apply_tenant_suspension_task.delay"
        ) as delay:
            tenant, queued = reactivate_tenant(self.tenant.id, actor=self.admin)

        self.assertEqual(tenant.status, TenantStatus.ACTIVE)
        self.assertTrue(tenant.can_use_mail)
        delay.assert_called_once_with(str(self.tenant.id), True)

    def test_reactivating_a_never_approved_workspace_is_refused(self):
        """
        This used to succeed and do nothing: status became ACTIVE while
        `can_use_mail` stayed False, with no explanation an operator would see.
        """
        pending = make_unapproved_tenant(self.owner, slug="pending-one")
        with self.assertRaises(ApprovalError) as ctx:
            reactivate_tenant(pending.id, actor=self.admin)
        self.assertIn("approve", str(ctx.exception).lower())


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class ProvisioningPathsAreGatedTest(TestCase):
    """
    Every route to the Mail Engine, not just the obvious one.

    A gate on the create view alone is a gate a new caller bypasses by not
    knowing it exists.
    """

    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@pending.example")
        self.tenant = make_unapproved_tenant(self.owner)
        self.api = auth_client(self.owner, self.tenant)

    def test_an_unapproved_workspace_cannot_add_a_domain(self):
        res = self.api.post(
            reverse("domain-list"), {"domain": "pending.example"}, format="json"
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Domain.objects.filter(tenant=self.tenant).count(), 0)

    def test_the_refusal_explains_itself_without_leaking(self):
        res = self.api.post(
            reverse("domain-list"), {"domain": "pending.example"}, format="json"
        )
        detail = res.json()["detail"].lower()
        self.assertIn("approv", detail)
        self.assertNotIn("status", detail)

    def test_the_celery_task_refuses_an_unapproved_workspace(self):
        """
        The gate lives at the task too, so a job queued before an approval was
        revoked cannot provision after it.
        """
        from apps.mail_engine.tasks import provision_domain_task

        domain = make_domain(self.tenant, "pending.example")
        with mock.patch("apps.mail_engine.factory.get_adapter") as get_adapter:
            provision_domain_task(str(domain.id))
            get_adapter.assert_not_called()

        domain.refresh_from_db()
        self.assertFalse(domain.mail_engine_provisioned)

    def test_an_approved_workspace_can_add_a_domain(self):
        """The gate must not be a wall — the happy path still works."""
        approve_tenant(
            self.tenant.id,
            actor=make_user("admin@matemail.online", is_staff=True),
        )
        res = self.api.post(
            reverse("domain-list"), {"domain": "approved.example"}, format="json"
        )
        self.assertEqual(res.status_code, 201)
