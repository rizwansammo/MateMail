"""
End-to-end SMTP policy: the real bridge, the real protocol, the real MateMail.

Every test here starts the actual `scripts/postfix_policy_bridge.py` server,
connects to it with a plain socket, and speaks the Postfix `check_policy_service`
protocol byte for byte — the same lines Postfix sends, parsed by the same code
that will parse Postfix's. The answers come from a live Django with a real
database and a real Redis behind it.

## What this proves, and what it does not

Proved here: given the attributes Postfix sends, MateMail's answer reaches
Postfix in the right form, for every case in the P5 relay matrix.

Not proved here: that Postfix sends those attributes at those stages. Postfix is
not running in CI. The configuration half is
`test_engine_policy_integration.py`, and the final confirmation is the
`postconf` output that `scripts/install-policy-bridge.sh` prints at deployment.

**No mail is sent by any test in this file.** The policy service is a decision
endpoint — it never touches a message — and the permitted cases stop at the
decision, which is exactly where Postfix would go on to do the delivery we are
not doing.
"""
import importlib.util
import pathlib
import socket
import threading
import unittest
import uuid
from unittest import mock

from django.test import LiveServerTestCase, override_settings

from apps.aliases.models import Alias, AliasStatus
from apps.domains.models import DomainStatus
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.platform_admin.approval import set_outbound_enabled, suspend_tenant
from apps.tenants.models import TenantStatus
from tests.factories import (
    make_domain,
    make_mailbox,
    make_plan,
    make_tenant,
    make_unapproved_tenant,
    make_user,
    subscribe,
)

BRIDGE_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "postfix_policy_bridge.py"
)
INTERNAL_SECRET = "protocol-test-internal-secret"
PLATFORM_SENDER = "noreply@mail.matemail.online"


def load_bridge():
    spec = importlib.util.spec_from_file_location("bridge_under_test", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PolicyProtocolClient:
    """
    A minimal Postfix policy client.

    Deliberately hand-written rather than mocked: the framing is the contract.
    Postfix sends `key=value` lines, a blank line, then reads a single
    `action=...` line followed by a blank line, and reuses the connection for
    the next request.
    """

    def __init__(self, host, port, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.reader = self.sock.makefile("rwb")

    def ask(self, **attrs) -> str:
        payload = "".join(f"{k}={v}\n" for k, v in attrs.items()) + "\n"
        self.reader.write(payload.encode())
        self.reader.flush()

        action = None
        while True:
            line = self.reader.readline()
            if not line:
                raise AssertionError("bridge closed the connection without answering")
            text = line.decode().rstrip("\r\n")
            if not text:
                break
            if text.startswith("action="):
                action = text[len("action="):]
        if action is None:
            raise AssertionError("no action= line in the bridge response")
        return action

    def close(self):
        try:
            self.reader.close()
            self.sock.close()
        except Exception:
            pass


@override_settings(
    INTERNAL_API_SECRET=INTERNAL_SECRET,
    PLATFORM_SENDER_ADDRESSES=[PLATFORM_SENDER],
    PLATFORM_SENDER_MAX_PER_HOUR=60,
)
class PolicyProtocolTestCase(LiveServerTestCase):
    """Boots the real bridge against the live Django for each test."""

    #: Point the bridge at a dead port to simulate MateMail being unreachable.
    matemail_reachable = True

    def setUp(self):
        super().setUp()
        self.bridge = load_bridge()
        self.bridge.INTERNAL_API_SECRET = INTERNAL_SECRET
        self.bridge.DJANGO_INTERNAL_URL = (
            self.live_server_url if self.matemail_reachable
            else "http://127.0.0.1:1"  # nothing listens here
        )
        self.bridge.REQUEST_TIMEOUT = 5

        self._bridges = []
        self._start_bridge()
        self._build_workspace()
        self._reset_rate_limit_counters()

    def _start_bridge(self):
        """
        Run the real bridge server on an ephemeral port in its own event loop.

        Shut down deliberately rather than by abandoning a daemon thread: an
        abandoned loop leaves pending accept tasks that surface later as
        unrelated noise in whichever test happens to be running.
        """
        import asyncio

        # Held in locals and captured by the closures below, NOT on self. Each
        # call owns its own loop, task and thread, so a second start cannot
        # leave the first one's cleanup pointing at the second one's loop —
        # which would silently abandon a running server.
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        started = {}

        async def serve():
            server = await asyncio.start_server(
                self.bridge.handle_client, "127.0.0.1", 0
            )
            started["server"] = server
            self.port = server.sockets[0].getsockname()[1]
            ready.set()
            try:
                await server.serve_forever()
            except asyncio.CancelledError:
                pass

        def run():
            asyncio.set_event_loop(loop)
            started["task"] = loop.create_task(serve())
            try:
                loop.run_until_complete(started["task"])
            except asyncio.CancelledError:
                pass
            finally:
                loop.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(10), "bridge did not start")

        def shutdown():
            """
            Stop the server and cancel the task in ONE scheduled callback.

            Scheduling them as two separate `call_soon_threadsafe` calls is a
            race, and it is the race that failed CI. Closing the server ends
            `serve_forever`, which ends `run_until_complete`, which runs the
            `finally` above and closes the loop — so by the time the second call
            is made the loop can already be closed, and `call_soon_threadsafe`
            raises `RuntimeError: Event loop is closed`.

            It is timing-dependent, which is why it survived a local run on
            Windows (proactor loop, slower teardown) and errored 8 of these 34
            tests on Linux. Doing both inside one callback means they happen in
            the same loop iteration, before the loop has any opportunity to
            close.

            The guard covers the remaining window: the loop could in principle
            have finished on its own between the check and the call, and there
            is nothing left to stop if it has.
            """
            def stop():
                server = started.get("server")
                if server is not None:
                    server.close()
                task = started.get("task")
                if task is not None:
                    task.cancel()

            try:
                loop.call_soon_threadsafe(stop)
            except RuntimeError:
                pass
            thread.join(timeout=10)
            self.assertFalse(
                thread.is_alive(),
                "the bridge thread did not stop — a later test would share the port",
            )

        # Recorded so `BridgeHarnessTeardownTest` can drive a real bridge's own
        # teardown rather than re-implementing it. Nothing else reads this.
        self._bridges.append(
            {"loop": loop, "thread": thread, "started": started, "shutdown": shutdown}
        )
        self.addCleanup(shutdown)

    def _build_workspace(self):
        self.plan = make_plan(
            f"protocol-{uuid.uuid4().hex[:8]}",
            max_messages_per_hour_per_mailbox=50,
            max_messages_per_day_per_tenant=500,
        )
        self.owner = make_user("owner@acme.example")
        self.tenant = make_tenant(self.owner, status=TenantStatus.ACTIVE)
        subscribe(self.tenant, self.plan)
        self.domain = make_domain(self.tenant, "acme.example", status=DomainStatus.ACTIVE)
        self.mailbox = make_mailbox(self.tenant, self.domain, local_part="alice")
        self.admin = make_user("admin@matemail.online", is_staff=True)

        # A second, unrelated workspace, for the cross-tenant cases.
        self.rival_owner = make_user("owner@rival.example")
        self.rival = make_tenant(
            self.rival_owner, name="Rival", slug="rival", status=TenantStatus.ACTIVE
        )
        subscribe(self.rival, self.plan)
        self.rival_domain = make_domain(
            self.rival, "rival.example", status=DomainStatus.ACTIVE
        )
        self.rival_mailbox = make_mailbox(
            self.rival, self.rival_domain, local_part="ceo"
        )

    def _reset_rate_limit_counters(self):
        """
        Clear this test's Redis counters, before and after.

        The database is rolled back between tests; Redis is not. Every test in
        this class uses the same mailbox address, so without this the counters
        carry over and a limit test sees a mailbox that has already "sent"
        whatever the previous tests submitted — failing, or worse, passing for
        the wrong reason.
        """
        from apps.smtp_policy.rate_limits import MailRateLimiter

        limiter = MailRateLimiter()

        def clear():
            for mailbox in (self.mailbox, self.rival_mailbox):
                limiter.reset(
                    mailbox_email=mailbox.email, tenant_id=str(mailbox.tenant_id)
                )
            limiter.redis.delete(f"ratelimit:platform:{PLATFORM_SENDER}")

        clear()
        self.addCleanup(clear)

    # ── Helpers shaped like what Postfix sends ──────────────────────────────

    def policy_client(self):
        c = PolicyProtocolClient("127.0.0.1", self.port)
        self.addCleanup(c.close)
        return c

    def submit(self, *, sasl, sender, recipient="someone@external.example",
               stage="RCPT", client_address="203.0.113.10"):
        """One authenticated submission, as Postfix would present it."""
        return self.policy_client().ask(
            request="smtpd_access_policy",
            protocol_state=stage,
            protocol_name="ESMTP",
            client_address=client_address,
            sasl_username=sasl,
            sender=sender,
            recipient=recipient,
        )

    def deliver(self, *, recipient, sender="stranger@outside.example",
                client_address="198.51.100.7"):
        """One inbound delivery from an external MTA — no SASL."""
        return self.policy_client().ask(
            request="smtpd_access_policy",
            protocol_state="RCPT",
            protocol_name="ESMTP",
            client_address=client_address,
            sasl_username="",
            sender=sender,
            recipient=recipient,
        )

    def assertPermitted(self, action):
        """
        DUNNO, never OK. OK would tell Postfix to stop evaluating and skip
        `reject_unauth_destination` — the anti-relay check that follows ours.
        """
        self.assertEqual(action, "DUNNO", f"expected a pass, got {action!r}")

    def assertRejected(self, action):
        self.assertTrue(
            action.startswith("REJECT"), f"expected a rejection, got {action!r}"
        )

    def assertDeferred(self, action):
        self.assertTrue(
            action.startswith("DEFER"), f"expected a deferral, got {action!r}"
        )


class RelayMatrixTest(PolicyProtocolTestCase):
    """The P5 safe-SMTP matrix, cases A through L."""

    # ── A. Unauthenticated external -> external ─────────────────────────────

    def test_A_unauthenticated_external_to_external_is_refused(self):
        """
        The open-relay case. MateMail refuses because it hosts neither the
        sender's domain nor the recipient's.

        Postfix would already have refused this at `reject_unauth_destination`
        in `smtpd_relay_restrictions`, which is evaluated independently and
        holds even when this service is down. This is the second of the two.
        """
        action = self.deliver(
            recipient="victim@somewhere-else.example",
            sender="spammer@outside.example",
        )
        self.assertRejected(action)

    # ── B. Invalid credentials ──────────────────────────────────────────────

    def test_B_a_client_without_valid_credentials_is_never_treated_as_authenticated(self):
        """
        Credential validation is Dovecot's job and happens before any policy
        service is consulted: a failed AUTH never reaches a MAIL FROM.

        What this service must guarantee is the other half — that an
        unauthenticated session cannot be mistaken for an authenticated one.
        `sasl_username` is set by Postfix from the SASL layer, so an empty value
        means "not authenticated" and must route to inbound policy, where an
        external recipient is refused.
        """
        action = self.deliver(
            recipient="anyone@external.example", sender="alice@acme.example"
        )
        self.assertRejected(action)

    # ── C. Authenticated, sending as itself ─────────────────────────────────

    def test_C_an_approved_mailbox_may_send_as_itself(self):
        action = self.submit(sasl=self.mailbox.email, sender=self.mailbox.email)
        self.assertPermitted(action)

    # ── D. Authenticated, sending as an authorized alias ────────────────────

    def test_D_an_authorized_alias_may_be_used_as_the_sender(self):
        """
        `sales@acme.example` delivers to alice, so alice may send as it. This
        mirrors the engine's own sender ACL, where an alias whose `goto` is the
        logged-in mailbox is a permitted sender — the two must agree, or mail
        the engine accepts is refused here (or the reverse).
        """
        alias = Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="sales@acme.example",
            destination_mailbox=self.mailbox,
        )
        action = self.submit(sasl=self.mailbox.email, sender=alias.source_address)
        self.assertPermitted(action)

    def test_D2_a_disabled_alias_may_not_be_used_as_the_sender(self):
        alias = Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="old@acme.example",
            destination_mailbox=self.mailbox,
            status=AliasStatus.DISABLED,
        )
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender=alias.source_address)
        )

    def test_D3_an_alias_belonging_to_someone_else_is_not_authorization(self):
        """
        The alias exists and is active, but it delivers to a different mailbox.
        Authorization is the `destination_mailbox` relationship, not the mere
        existence of an address.
        """
        colleague = make_mailbox(self.tenant, self.domain, local_part="bob")
        alias = Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="support@acme.example",
            destination_mailbox=colleague,
        )
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender=alias.source_address)
        )

    # ── E. Cross-tenant spoofing ────────────────────────────────────────────

    def test_E_a_mailbox_cannot_send_as_another_tenants_address(self):
        """
        The multi-tenant form of an open relay: one customer sending as another
        customer's domain, from infrastructure that would DKIM-sign it.
        """
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender=self.rival_mailbox.email)
        )

    def test_E2_a_cross_tenant_alias_grants_nothing(self):
        """
        An alias on the rival's domain, delivering to the rival's mailbox.
        Alice authenticating cannot borrow it.
        """
        Alias.objects.create(
            tenant=self.rival,
            domain=self.rival_domain,
            source_address="sales@rival.example",
            destination_mailbox=self.rival_mailbox,
        )
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender="sales@rival.example")
        )

    def test_E3_an_unhosted_sender_is_refused(self):
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender="ceo@some-bank.example")
        )

    # ── F. Unapproved tenant ────────────────────────────────────────────────

    def test_F_an_unapproved_workspace_cannot_send(self):
        """
        Outbound refusals are permanent by design: the customer is at a mail
        client and is better served by an immediate answer than by silent
        queuing. See `docs/MAIL_POLICY.md`.
        """
        pending = make_unapproved_tenant(make_user("owner@pending.example"))
        subscribe(pending, self.plan)
        domain = make_domain(pending, "pending.example", status=DomainStatus.ACTIVE)
        mailbox = make_mailbox(pending, domain, local_part="dave")
        self.assertRejected(self.submit(sasl=mailbox.email, sender=mailbox.email))

    # ── G. Suspended tenant ─────────────────────────────────────────────────

    def test_G_a_suspended_workspace_cannot_send(self):
        with mock.patch("apps.mail_engine.tasks.apply_tenant_suspension_task.delay"):
            suspend_tenant(self.tenant.id, actor=self.admin, reason="abuse")
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender=self.mailbox.email)
        )

    def test_G2_a_suspended_workspace_still_receives_but_deferred(self):
        """
        Inbound defers rather than bouncing: suspension is reversible, and the
        sender's message should survive it.
        """
        with mock.patch("apps.mail_engine.tasks.apply_tenant_suspension_task.delay"):
            suspend_tenant(self.tenant.id, actor=self.admin)
        self.assertDeferred(self.deliver(recipient=self.mailbox.email))

    # ── H. Outbound disabled ────────────────────────────────────────────────

    def test_H_outbound_disabled_stops_sending(self):
        set_outbound_enabled(self.tenant.id, enabled=False, actor=self.admin)
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender=self.mailbox.email)
        )

    def test_H2_outbound_disabled_does_not_stop_receiving(self):
        """Cutting inbound punishes the people writing to the customer."""
        set_outbound_enabled(self.tenant.id, enabled=False, actor=self.admin)
        self.assertPermitted(self.deliver(recipient=self.mailbox.email))

    # ── I. Suspended mailbox ────────────────────────────────────────────────

    def test_I_a_suspended_mailbox_cannot_send(self):
        Mailbox.objects.filter(pk=self.mailbox.pk).update(
            status=MailboxStatus.SUSPENDED
        )
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender=self.mailbox.email)
        )

    def test_I2_a_disabled_mailbox_cannot_send(self):
        Mailbox.objects.filter(pk=self.mailbox.pk).update(status=MailboxStatus.DISABLED)
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender=self.mailbox.email)
        )

    # ── J. Rate limits ──────────────────────────────────────────────────────

    def test_J_exceeding_the_plan_limit_defers(self):
        """
        DEFER, not REJECT. The sender is within policy and merely early, so the
        message is retried rather than destroyed.
        """
        tight = make_plan(
            f"tight-{uuid.uuid4().hex[:8]}",
            max_messages_per_hour_per_mailbox=2,
            max_messages_per_day_per_tenant=500,
        )
        subscribe(self.tenant, tight)

        for _ in range(2):
            self.assertPermitted(
                self.submit(
                    sasl=self.mailbox.email, sender=self.mailbox.email,
                    stage="END-OF-MESSAGE",
                )
            )
        self.assertDeferred(
            self.submit(
                sasl=self.mailbox.email, sender=self.mailbox.email,
                stage="END-OF-MESSAGE",
            )
        )

    def test_J2_the_rcpt_stage_does_not_consume_quota(self):
        """
        RCPT fires once per recipient. If it counted, one message to three
        people would cost three — and a tight plan would refuse ordinary mail.
        """
        tight = make_plan(
            f"tight-{uuid.uuid4().hex[:8]}",
            max_messages_per_hour_per_mailbox=2,
            max_messages_per_day_per_tenant=500,
        )
        subscribe(self.tenant, tight)

        for recipient in ("a@x.example", "b@x.example", "c@x.example", "d@x.example"):
            self.assertPermitted(
                self.submit(
                    sasl=self.mailbox.email, sender=self.mailbox.email,
                    recipient=recipient, stage="RCPT",
                )
            )
        # Four RCPTs consumed nothing, so the message itself still passes.
        self.assertPermitted(
            self.submit(
                sasl=self.mailbox.email, sender=self.mailbox.email,
                stage="END-OF-MESSAGE",
            )
        )

    def test_J3_quota_is_charged_to_the_authenticated_mailbox_not_the_alias(self):
        """
        Otherwise one account spreads its hourly quota across every alias it
        holds — the cheap-identity problem the alias cap exists to prevent.
        """
        tight = make_plan(
            f"tight-{uuid.uuid4().hex[:8]}",
            max_messages_per_hour_per_mailbox=2,
            max_messages_per_day_per_tenant=500,
        )
        subscribe(self.tenant, tight)
        alias = Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="sales@acme.example",
            destination_mailbox=self.mailbox,
        )

        self.assertPermitted(
            self.submit(sasl=self.mailbox.email, sender=self.mailbox.email,
                        stage="END-OF-MESSAGE")
        )
        self.assertPermitted(
            self.submit(sasl=self.mailbox.email, sender=alias.source_address,
                        stage="END-OF-MESSAGE")
        )
        # Third message, whichever identity it claims.
        self.assertDeferred(
            self.submit(sasl=self.mailbox.email, sender=alias.source_address,
                        stage="END-OF-MESSAGE")
        )

    # ── K. The platform sender ──────────────────────────────────────────────

    def test_K_the_platform_sender_is_permitted(self):
        self.assertPermitted(
            self.submit(sasl=PLATFORM_SENDER, sender=PLATFORM_SENDER,
                        client_address="10.244.0.247")
        )

    def test_K2_the_platform_sender_is_still_rate_limited(self):
        """
        Trusted to send is not trusted to send without bound. The ceiling
        matches the limit the engine already holds on that mailbox (60/hour,
        measured), so the two agree.
        """
        with override_settings(PLATFORM_SENDER_MAX_PER_HOUR=3):
            for _ in range(3):
                self.assertPermitted(
                    self.submit(sasl=PLATFORM_SENDER, sender=PLATFORM_SENDER,
                                stage="END-OF-MESSAGE")
                )
            self.assertDeferred(
                self.submit(sasl=PLATFORM_SENDER, sender=PLATFORM_SENDER,
                            stage="END-OF-MESSAGE")
            )

    def test_K3_the_exception_is_identity_specific_not_domain_wide(self):
        """
        `anything@mail.matemail.online` must not inherit it. The platform's own
        sending domain will hold other addresses one day.
        """
        for impostor in (
            "someone-else@mail.matemail.online",
            "noreply@matemail.online",
            "noreply@evil-mail.matemail.online.attacker.example",
        ):
            with self.subTest(impostor=impostor):
                self.assertRejected(self.submit(sasl=impostor, sender=impostor))

    def test_K4_a_customer_credential_cannot_claim_the_platform_identity(self):
        self.assertRejected(
            self.submit(sasl=self.mailbox.email, sender=PLATFORM_SENDER)
        )

    def test_K5_the_platform_credential_cannot_send_as_anyone_else(self):
        self.assertRejected(
            self.submit(sasl=PLATFORM_SENDER, sender="ceo@some-bank.example")
        )


class MateMailUnreachableTest(PolicyProtocolTestCase):
    """Case L — the failure mode that must never become an open relay."""

    matemail_reachable = False

    def test_L_an_outage_defers_and_never_permits(self):
        action = self.submit(sasl=self.mailbox.email, sender=self.mailbox.email)
        self.assertDeferred(action)
        self.assertNotEqual(action, "DUNNO")

    def test_L2_an_outage_defers_inbound_too(self):
        self.assertDeferred(self.deliver(recipient=self.mailbox.email))

    def test_L3_an_outage_never_answers_OK(self):
        """
        `OK` would skip `reject_unauth_destination` — so an outage would not
        merely fail open, it would disable the anti-relay check itself.
        """
        for action in (
            self.submit(sasl=self.mailbox.email, sender=self.mailbox.email),
            self.deliver(recipient="anyone@anywhere.example"),
        ):
            self.assertNotEqual(action.split()[0], "OK")

    def test_L4_a_spoofing_attempt_during_an_outage_is_not_permitted(self):
        action = self.submit(sasl=self.mailbox.email, sender=self.rival_mailbox.email)
        self.assertNotEqual(action, "DUNNO")


class WrongSecretTest(PolicyProtocolTestCase):
    """A misconfigured shared secret must fail closed, not open."""

    def test_a_wrong_internal_secret_defers_rather_than_permitting(self):
        self.bridge.INTERNAL_API_SECRET = "not-the-right-secret"
        action = self.submit(sasl=self.mailbox.email, sender=self.mailbox.email)
        self.assertDeferred(action)

    def test_an_empty_internal_secret_defers_rather_than_permitting(self):
        self.bridge.INTERNAL_API_SECRET = ""
        self.assertDeferred(
            self.submit(sasl=self.mailbox.email, sender=self.mailbox.email)
        )


class ProtocolFramingTest(PolicyProtocolTestCase):
    """The wire format itself — where a subtle bug desynchronises a connection."""

    def test_a_single_connection_serves_many_requests(self):
        """
        Postfix keeps the connection open across messages. A daemon that
        answered once and stopped reading would stall the queue.
        """
        client = self.policy_client()
        for _ in range(5):
            action = client.ask(
                request="smtpd_access_policy",
                protocol_state="RCPT",
                sasl_username=self.mailbox.email,
                sender=self.mailbox.email,
                recipient="someone@external.example",
            )
            self.assertPermitted(action)

    def test_the_response_is_one_action_line_and_a_blank_line(self):
        client = self.policy_client()
        client.reader.write(
            b"request=smtpd_access_policy\nprotocol_state=CONNECT\n\n"
        )
        client.reader.flush()
        self.assertEqual(client.reader.readline(), b"action=DUNNO\n")
        self.assertEqual(client.reader.readline(), b"\n")

    def test_unknown_attributes_are_ignored(self):
        """Postfix adds attributes between versions; unknown ones must not break us."""
        action = self.policy_client().ask(
            request="smtpd_access_policy",
            protocol_state="RCPT",
            sasl_username=self.mailbox.email,
            sender=self.mailbox.email,
            recipient="someone@external.example",
            some_future_attribute="whatever",
            ccert_subject="",
            encryption_keysize="256",
        )
        self.assertPermitted(action)

    def test_an_empty_request_does_not_crash_the_connection(self):
        client = self.policy_client()
        client.reader.write(b"\n")
        client.reader.flush()
        action = client.ask(
            request="smtpd_access_policy",
            protocol_state="RCPT",
            sasl_username=self.mailbox.email,
            sender=self.mailbox.email,
            recipient="someone@external.example",
        )
        self.assertPermitted(action)


if __name__ == "__main__":
    unittest.main()


class BridgeHarnessTeardownTest(PolicyProtocolTestCase):
    """
    Regression cover for the teardown defect that failed CI.

    The original cleanup scheduled `server.close()` and `task.cancel()` as two
    separate `call_soon_threadsafe` calls. Closing the server ends
    `serve_forever`, which ends `run_until_complete`, which closes the loop — so
    if the loop thread completes all of that between the two calls, the second
    lands on a closed loop and raises `RuntimeError: Event loop is closed`.

    ## Why this is asserted structurally rather than by stress

    The first attempt at this test ran 25 start/stop cycles and asserted no
    error. That was measured against the buggy teardown on Linux/Python 3.11 and
    **it passed** — the race needs the main thread to be preempted in the
    one-bytecode window between the two calls, which needs contention this test
    does not create. CI lost it 8 times in 34 only because the full 977-test
    suite was running on a two-core runner.

    A test that can only fail by losing a race is not coverage, so it was
    replaced with the two properties below, both of which fail deterministically
    on any machine if the defect returns.
    """

    def _handles(self):
        self.assertTrue(self._bridges, "no bridge was recorded")
        return self._bridges[-1]

    def test_the_teardown_schedules_exactly_one_threadsafe_callback(self):
        """
        One call, so nothing can happen to the loop between two of them.

        This is the invariant, stated directly: the defect was a *second*
        `call_soon_threadsafe`, and there is no safe number of them above one.
        """
        handles = self._handles()
        loop = handles["loop"]

        calls = []
        real = loop.call_soon_threadsafe

        def counting(callback, *args, **kwargs):
            calls.append(callback)
            return real(callback, *args, **kwargs)

        loop.call_soon_threadsafe = counting
        try:
            # Run the cleanup this bridge registered, for real.
            self.doCleanups()
        finally:
            loop.call_soon_threadsafe = real

        self.assertEqual(
            len(calls), 1,
            f"teardown scheduled {len(calls)} threadsafe callbacks; exactly one "
            "is required, because the loop can close between any two",
        )

    def test_the_teardown_tolerates_a_loop_that_has_already_closed(self):
        """
        The remaining window: the loop could finish on its own just before the
        cleanup runs. Scheduling onto it must not raise out of teardown — an
        exception there is reported as an ERROR on a test whose body passed,
        which is exactly how the original defect presented in CI.
        """
        handles = self._handles()

        # Shut the bridge down for real: the thread exits and the loop closes.
        self.doCleanups()
        self.assertTrue(
            handles["loop"].is_closed(), "the loop should be closed after teardown"
        )

        # Now run that same teardown again, against a genuinely closed loop.
        # This is the real situation, not a simulated one, and it must not raise.
        handles["shutdown"]()

    def test_each_start_gets_its_own_loop_thread_and_port(self):
        """
        Proves the cleanups are independent rather than all pointing at the most
        recent loop — the latent bug that made instance attributes unsafe here,
        and which WOULD have silently abandoned a running server.
        """
        ports = {self.port}
        loops = {id(self._handles()["loop"])}
        for _ in range(4):
            self._start_bridge()
            ports.add(self.port)
            loops.add(id(self._handles()["loop"]))
            self.assertPermitted(
                self.submit(sasl=self.mailbox.email, sender=self.mailbox.email)
            )
        self.assertEqual(len(ports), 5, f"ports were reused: {ports}")
        self.assertEqual(len(loops), 5, "a loop object was shared between starts")
