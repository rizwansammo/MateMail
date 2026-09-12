"""
The SMTP policy bridge — where MateMail decides what the Mail Engine may carry.

These are the checks that stand between an authenticated account and the
Internet. They are tested against the real endpoints, with the real policy
module behind them, because a bridge that returns OK for everything would pass
any test that only asserted the endpoint exists.

Nothing here sends mail. The bridge is a decision service: it answers OK,
REJECT or DEFER over HTTP, and those three answers are the entire surface.
"""
from unittest import mock

from django.test import TestCase, override_settings

from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.domains.models import DomainStatus
from apps.platform_admin.approval import set_outbound_enabled, suspend_tenant
from apps.tenants.models import TenantStatus
from tests.factories import (
    make_domain,
    make_mailbox,
    make_tenant,
    make_unapproved_tenant,
    make_user,
)

INTERNAL_SECRET = "test-only-internal-secret"
PLATFORM_SENDER = "noreply@mail.matemail.online"

INBOUND = "/api/internal/smtp/inbound/"
OUTBOUND = "/api/internal/smtp/outbound/"


@override_settings(
    INTERNAL_API_SECRET=INTERNAL_SECRET,
    PLATFORM_SENDER_ADDRESSES=[PLATFORM_SENDER],
)
class PolicyBridgeTestCase(TestCase):
    """Shared setup: one approved workspace with one active mailbox."""

    def setUp(self):
        self.owner = make_user("owner@acme.example")
        self.tenant = make_tenant(self.owner, status=TenantStatus.ACTIVE)
        self.domain = make_domain(
            self.tenant, "acme.example", status=DomainStatus.ACTIVE
        )
        self.mailbox = make_mailbox(self.tenant, self.domain, local_part="alice")

        # The limiter talks to Redis. Every test here is about policy, not about
        # counting, so it is allowed by default and asserted on explicitly in
        # the tests that are about limits.
        patcher = mock.patch(
            "apps.smtp_policy.views._limiter.check_and_record",
            return_value=(True, ""),
        )
        self.limiter = patcher.start()
        self.addCleanup(patcher.stop)

        platform_patcher = mock.patch(
            "apps.smtp_policy.views._limiter.check_and_record_platform",
            return_value=(True, ""),
        )
        self.platform_limiter = platform_patcher.start()
        self.addCleanup(platform_patcher.stop)

    def ask(self, url, payload, *, secret=INTERNAL_SECRET):
        headers = {"HTTP_X_INTERNAL_SECRET": secret} if secret is not None else {}
        return self.client.post(
            url, payload, content_type="application/json", **headers
        )

    def outbound(self, sender, sasl_username=None, **kw):
        return self.ask(
            OUTBOUND,
            {"sender": sender, "sasl_username": sasl_username or sender},
            **kw,
        )

    def inbound(self, recipient, **kw):
        return self.ask(INBOUND, {"recipient": recipient}, **kw)


class NoOpenRelayTest(PolicyBridgeTestCase):
    """
    MateMail must never relay for a sender it does not host.

    An open relay is the single fastest way to lose a sending reputation
    permanently, and the damage is done long before anyone notices.
    """

    def test_an_unknown_sender_is_refused(self):
        res = self.outbound("stranger@somewhere-else.example")
        self.assertEqual(res.json()["action"], "REJECT")

    def test_a_sender_on_a_domain_we_do_not_host_is_refused(self):
        res = self.outbound("alice@not-our-domain.example")
        self.assertEqual(res.json()["action"], "REJECT")

    def test_a_deleted_mailbox_is_refused(self):
        Mailbox.objects.filter(pk=self.mailbox.pk).delete()
        res = self.outbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "REJECT")

    def test_an_empty_sender_is_refused(self):
        self.assertEqual(self.outbound("", "").json()["action"], "REJECT")

    def test_the_bridge_itself_requires_the_internal_secret(self):
        """
        Without this the policy service is an unauthenticated endpoint that
        tells the world which addresses exist.
        """
        for secret in (None, "", "wrong-secret"):
            with self.subTest(secret=secret):
                res = self.outbound(self.mailbox.email, secret=secret)
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.json()["action"], "REJECT")

    def test_a_hosted_active_sender_is_allowed(self):
        """The refusals above must be about policy, not a bridge that says no."""
        res = self.outbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "OK")


class SenderAuthorizationTest(PolicyBridgeTestCase):
    """
    An authenticated account may send as itself and as nothing else.

    Cross-tenant spoofing is the multi-tenant version of an open relay: one
    customer sending as another customer's domain, from infrastructure that
    will DKIM-sign it.
    """

    def setUp(self):
        super().setUp()
        self.other_owner = make_user("owner@rival.example")
        self.other_tenant = make_tenant(
            self.other_owner, name="Rival", slug="rival", status=TenantStatus.ACTIVE
        )
        self.other_domain = make_domain(
            self.other_tenant, "rival.example", status=DomainStatus.ACTIVE
        )
        self.other_mailbox = make_mailbox(
            self.other_tenant, self.other_domain, local_part="bob"
        )

    def test_a_mailbox_cannot_send_as_another_tenants_mailbox(self):
        res = self.outbound(
            sender=self.other_mailbox.email, sasl_username=self.mailbox.email
        )
        self.assertEqual(res.json()["action"], "REJECT")

    def test_a_mailbox_cannot_send_as_a_colleague(self):
        """Same workspace is not the same identity."""
        colleague = make_mailbox(self.tenant, self.domain, local_part="carol")
        res = self.outbound(sender=colleague.email, sasl_username=self.mailbox.email)
        self.assertEqual(res.json()["action"], "REJECT")

    def test_a_mailbox_cannot_send_as_the_platform(self):
        """
        The platform allowance is keyed on the SENDER, and the sender must equal
        the authenticated account — so holding a customer credential does not
        let anyone send as MateMail itself.
        """
        res = self.outbound(sender=PLATFORM_SENDER, sasl_username=self.mailbox.email)
        self.assertEqual(res.json()["action"], "REJECT")

    def test_case_and_whitespace_do_not_defeat_the_comparison(self):
        res = self.outbound(
            sender=f"  {self.mailbox.email.upper()}  ",
            sasl_username=self.mailbox.email,
        )
        self.assertEqual(res.json()["action"], "OK")


class OutboundPolicyEnforcementTest(PolicyBridgeTestCase):
    """Suspension, approval and the outbound kill switch, at submission time."""

    def test_a_suspended_workspace_cannot_send(self):
        with mock.patch("apps.mail_engine.tasks.apply_tenant_suspension_task.delay"):
            suspend_tenant(
                self.tenant.id,
                actor=make_user("admin@matemail.online", is_staff=True),
                reason="abuse",
            )
        res = self.outbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "REJECT")

    def test_outbound_disabled_stops_sending(self):
        set_outbound_enabled(
            self.tenant.id,
            enabled=False,
            actor=make_user("admin@matemail.online", is_staff=True),
        )
        res = self.outbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "REJECT")

    def test_an_unapproved_workspace_cannot_send(self):
        pending = make_unapproved_tenant(make_user("owner@pending.example"))
        domain = make_domain(pending, "pending.example", status=DomainStatus.ACTIVE)
        mailbox = make_mailbox(pending, domain, local_part="dave")
        res = self.outbound(mailbox.email)
        self.assertEqual(res.json()["action"], "REJECT")

    def test_a_suspended_mailbox_cannot_send(self):
        Mailbox.objects.filter(pk=self.mailbox.pk).update(
            status=MailboxStatus.SUSPENDED
        )
        res = self.outbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "REJECT")

    def test_a_disabled_mailbox_cannot_send(self):
        Mailbox.objects.filter(pk=self.mailbox.pk).update(status=MailboxStatus.DISABLED)
        res = self.outbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "REJECT")

    def test_an_inactive_sending_domain_stops_sending(self):
        self.domain.status = DomainStatus.PAUSED
        self.domain.save(update_fields=["status"])
        res = self.outbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "REJECT")

    def test_a_rate_limited_sender_defers_rather_than_bounces(self):
        """
        The sender is within policy and merely early. A REJECT here would
        destroy a legitimate message for being the 51st of the hour.
        """
        self.limiter.return_value = (False, "limit reached")
        res = self.outbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "DEFER")

    def test_policy_refusals_never_leak_internal_detail(self):
        with mock.patch("apps.mail_engine.tasks.apply_tenant_suspension_task.delay"):
            suspend_tenant(
                self.tenant.id,
                actor=make_user("admin@matemail.online", is_staff=True),
            )
        reason = self.outbound(self.mailbox.email).json()["reason"].lower()
        for word in ("tenant", "mailcow", "traceback", "postfix"):
            with self.subTest(word=word):
                self.assertNotIn(word, reason)


class PlatformSenderTest(PolicyBridgeTestCase):
    """
    MateMail's own service identity (Stage 18).

    It has no Mailbox, no Domain and no tenant, so without an explicit
    allowance every check in the bridge rejects it — and account recovery for
    every customer stops the moment this service is enforced in the engine's
    restrictions.
    """

    def test_the_platform_sender_is_allowed(self):
        res = self.outbound(PLATFORM_SENDER)
        self.assertEqual(res.json()["action"], "OK")

    def test_it_does_not_need_a_mailbox_row(self):
        self.assertFalse(Mailbox.objects.filter(email=PLATFORM_SENDER).exists())
        self.assertEqual(self.outbound(PLATFORM_SENDER).json()["action"], "OK")

    def test_a_display_name_in_the_setting_still_matches(self):
        """DEFAULT_FROM_EMAIL carries one, and the setting may be copied from it."""
        with override_settings(
            PLATFORM_SENDER_ADDRESSES=[f"MateMail <{PLATFORM_SENDER}>"]
        ):
            self.assertEqual(self.outbound(PLATFORM_SENDER).json()["action"], "OK")

    def test_it_is_still_held_to_sender_equals_authenticated(self):
        res = self.outbound(
            sender="anyone@elsewhere.example", sasl_username=PLATFORM_SENDER
        )
        self.assertEqual(res.json()["action"], "REJECT")

    def test_it_is_not_exempt_from_rate_limiting(self):
        """Trusted to send is not the same as trusted to send without bound."""
        self.platform_limiter.return_value = (False, "Platform sending limit reached.")
        res = self.outbound(PLATFORM_SENDER)
        self.assertEqual(res.json()["action"], "DEFER")

    def test_a_tenant_suspension_cannot_silence_the_platform(self):
        """
        An abuse response against one customer must not take MateMail's own
        password resets with it.
        """
        with mock.patch("apps.mail_engine.tasks.apply_tenant_suspension_task.delay"):
            suspend_tenant(
                self.tenant.id,
                actor=make_user("admin@matemail.online", is_staff=True),
            )
        self.assertEqual(self.outbound(PLATFORM_SENDER).json()["action"], "OK")

    def test_an_unconfigured_platform_sender_grants_nothing(self):
        with override_settings(PLATFORM_SENDER_ADDRESSES=[]):
            res = self.outbound(PLATFORM_SENDER)
            self.assertEqual(res.json()["action"], "REJECT")


class InboundBounceClassificationTest(PolicyBridgeTestCase):
    """
    Stage 14: reversible conditions must DEFER (4xx), not REJECT (5xx).

    A remote server told 5xx returns the message to its sender immediately.
    Told 4xx it holds and retries for days. For a workspace suspended over a
    billing question on a Friday, that is the difference between mail delivered
    on Monday and mail destroyed.
    """

    def test_a_hosted_active_recipient_is_accepted(self):
        res = self.inbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "OK")

    def test_an_unknown_domain_is_permanently_rejected(self):
        res = self.inbound("someone@not-hosted-here.example")
        self.assertEqual(res.json()["action"], "REJECT")

    def test_a_nonexistent_mailbox_is_permanently_rejected(self):
        """
        The one case that genuinely should bounce. Deferring here would make
        MateMail a backscatter source and hide typos from senders for days.
        """
        res = self.inbound("nobody@acme.example")
        self.assertEqual(res.json()["action"], "REJECT")

    def test_a_suspended_workspace_defers_rather_than_bouncing(self):
        with mock.patch("apps.mail_engine.tasks.apply_tenant_suspension_task.delay"):
            suspend_tenant(
                self.tenant.id,
                actor=make_user("admin@matemail.online", is_staff=True),
            )
        res = self.inbound(self.mailbox.email)
        self.assertEqual(res.json()["action"], "DEFER")

    def test_a_suspended_mailbox_defers(self):
        Mailbox.objects.filter(pk=self.mailbox.pk).update(
            status=MailboxStatus.SUSPENDED
        )
        self.assertEqual(self.inbound(self.mailbox.email).json()["action"], "DEFER")

    def test_a_disabled_mailbox_defers(self):
        Mailbox.objects.filter(pk=self.mailbox.pk).update(status=MailboxStatus.DISABLED)
        self.assertEqual(self.inbound(self.mailbox.email).json()["action"], "DEFER")

    def test_an_inactive_domain_defers(self):
        self.domain.status = DomainStatus.PAUSED
        self.domain.save(update_fields=["status"])
        self.assertEqual(self.inbound(self.mailbox.email).json()["action"], "DEFER")

    def test_a_cancelled_workspace_is_permanently_rejected(self):
        """A settled decision. The address really is not coming back."""
        self.tenant.status = TenantStatus.CANCELLED
        self.tenant.save(update_fields=["status"])
        self.assertEqual(self.inbound(self.mailbox.email).json()["action"], "REJECT")

    def test_outbound_disabled_does_not_stop_incoming_mail(self):
        """
        Cutting inbound as an abuse response punishes the people writing to
        the customer, and loses mail that was never the problem.
        """
        set_outbound_enabled(
            self.tenant.id,
            enabled=False,
            actor=make_user("admin@matemail.online", is_staff=True),
        )
        self.assertEqual(self.inbound(self.mailbox.email).json()["action"], "OK")

    def test_inbound_requires_the_internal_secret(self):
        res = self.inbound(self.mailbox.email, secret="wrong")
        self.assertEqual(res.status_code, 403)


class InboundAliasTest(PolicyBridgeTestCase):
    """
    An alias has no Mailbox row.

    A mailbox-only lookup rejected every alias address as nonexistent, which
    would permanently bounce all mail to every alias the product lets customers
    create.
    """

    def setUp(self):
        super().setUp()
        from apps.aliases.models import Alias, AliasStatus

        self.AliasStatus = AliasStatus
        self.alias = Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="sales@acme.example",
            destination_mailbox=self.mailbox,
        )

    def test_mail_to_an_active_alias_is_accepted(self):
        res = self.inbound(self.alias.source_address)
        self.assertEqual(res.json()["action"], "OK")

    def test_a_disabled_alias_defers_rather_than_bouncing(self):
        self.alias.status = self.AliasStatus.DISABLED
        self.alias.save(update_fields=["status"])
        self.assertEqual(
            self.inbound(self.alias.source_address).json()["action"], "DEFER"
        )

    def test_an_address_that_is_neither_mailbox_nor_alias_still_rejects(self):
        """The alias lookup must not turn the bridge into a catch-all."""
        res = self.inbound("definitely-nobody@acme.example")
        self.assertEqual(res.json()["action"], "REJECT")

    def test_an_alias_on_a_suspended_workspace_does_not_bypass_policy(self):
        """
        The workspace check runs before the recipient lookup, so an alias
        cannot be a route around it.
        """
        with mock.patch("apps.mail_engine.tasks.apply_tenant_suspension_task.delay"):
            suspend_tenant(
                self.tenant.id,
                actor=make_user("admin@matemail.online", is_staff=True),
            )
        self.assertEqual(
            self.inbound(self.alias.source_address).json()["action"], "DEFER"
        )
