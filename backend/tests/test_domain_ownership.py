"""
Domain ownership verification and the provisioning gate.

The P3 invariant: **an unverified domain can never be provisioned**. Before
P3 anyone could type a competitor's domain into MateMail and the platform
would immediately push it into the mail engine.

These tests prove the gate at every layer, not just the view, and prove that
ownership is exclusive across tenants at the database level.
"""
from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from apps.domains.models import Domain, DomainOwnership
from apps.domains.verification import (
    DomainNotVerified,
    assert_provisionable,
    check_ownership_dns,
    ensure_verification_token,
    generate_verification_token,
    rotate_verification_token,
    verify_domain_ownership,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_unverified_domain,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ownership-tests",
    }
}


def txt_answer(*values):
    """Fake dnspython TXT rdata objects."""
    return [mock.Mock(strings=[v.encode()]) for v in values]


class TokenTest(TestCase):
    def test_tokens_are_unguessable_and_unique(self):
        tokens = {generate_verification_token() for _ in range(200)}
        self.assertEqual(len(tokens), 200, "tokens collided")
        for t in tokens:
            self.assertTrue(t.startswith("matemail-verify-"))
            # 32 bytes of urlsafe entropy is ~43 chars on top of the prefix.
            self.assertGreaterEqual(len(t), 50)

    def test_token_is_stable_across_retries(self):
        """A customer waiting on propagation must not have the value change."""
        owner = make_user("tok@example.test")
        tenant = make_tenant(owner, name="Tok", slug="tok")
        domain = make_unverified_domain(tenant, "stable.example")
        first = ensure_verification_token(domain)
        self.assertEqual(ensure_verification_token(domain), first)
        self.assertEqual(ensure_verification_token(domain), first)

    def test_rotation_issues_a_new_token_and_unverifies(self):
        owner = make_user("rot@example.test")
        tenant = make_tenant(owner, name="Rot", slug="rot")
        domain = make_domain(tenant, "rotate.example")  # verified
        before = domain.verification_token
        self.assertTrue(domain.is_ownership_verified)

        rotate_verification_token(domain)
        domain.refresh_from_db()

        self.assertNotEqual(domain.verification_token, before)
        self.assertFalse(domain.is_ownership_verified)
        self.assertIsNone(domain.ownership_verified_at)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class DnsCheckTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("dns@example.test")
        self.tenant = make_tenant(self.owner, name="Dns", slug="dns")
        self.domain = make_unverified_domain(self.tenant, "check.example")
        self.token = ensure_verification_token(self.domain)

    def test_correct_txt_verifies(self):
        with mock.patch("dns.resolver.resolve", return_value=txt_answer(self.token)):
            verified, message = verify_domain_ownership(self.domain)
        self.assertTrue(verified, message)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.ownership_status, DomainOwnership.VERIFIED)
        self.assertIsNotNone(self.domain.ownership_verified_at)

    def test_wrong_txt_fails(self):
        with mock.patch("dns.resolver.resolve", return_value=txt_answer("some-other-value")):
            verified, message = verify_domain_ownership(self.domain)
        self.assertFalse(verified)
        self.assertIn("does not match", message)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.ownership_status, DomainOwnership.PENDING)

    def test_missing_txt_fails(self):
        import dns.resolver

        with mock.patch("dns.resolver.resolve", side_effect=dns.resolver.NXDOMAIN()):
            verified, message = verify_domain_ownership(self.domain)
        self.assertFalse(verified)
        self.assertIn("No verification record found", message)

    def test_no_answer_fails(self):
        import dns.resolver

        with mock.patch("dns.resolver.resolve", side_effect=dns.resolver.NoAnswer()):
            verified, _ = verify_domain_ownership(self.domain)
        self.assertFalse(verified)

    def test_resolver_timeout_is_reported_as_temporary(self):
        import dns.exception

        with mock.patch("dns.resolver.resolve", side_effect=dns.exception.Timeout()):
            verified, message = verify_domain_ownership(self.domain)
        self.assertFalse(verified)
        self.assertIn("temporary", message.lower())

    def test_substring_match_is_not_enough(self):
        """A catch-all TXT containing the token must not satisfy the challenge."""
        sneaky = f"v=spf1 include:evil.example {self.token} ~all"
        with mock.patch("dns.resolver.resolve", return_value=txt_answer(sneaky)):
            verified, _ = verify_domain_ownership(self.domain)
        self.assertFalse(verified, "a substring match was accepted")

    def test_token_survives_a_failed_check(self):
        with mock.patch("dns.resolver.resolve", return_value=txt_answer("nope")):
            verify_domain_ownership(self.domain)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.verification_token, self.token)

    def test_multi_string_txt_is_reassembled(self):
        """TXT values over 255 bytes arrive split and must be joined, not treated separately."""
        half = len(self.token) // 2
        rdata = mock.Mock(strings=[self.token[:half].encode(), self.token[half:].encode()])
        with mock.patch("dns.resolver.resolve", return_value=[rdata]):
            verified, _ = verify_domain_ownership(self.domain)
        self.assertTrue(verified)

    def test_verification_is_idempotent(self):
        with mock.patch("dns.resolver.resolve", return_value=txt_answer(self.token)):
            self.assertTrue(verify_domain_ownership(self.domain)[0])
            self.assertTrue(verify_domain_ownership(self.domain)[0])

    def test_check_performs_no_writes(self):
        with mock.patch("dns.resolver.resolve", return_value=txt_answer(self.token)):
            check_ownership_dns(self.domain)
        self.domain.refresh_from_db()
        self.assertEqual(self.domain.ownership_status, DomainOwnership.PENDING)

    def test_failure_message_is_matemail_authored(self):
        """Resolver internals must not reach the customer."""
        import dns.resolver

        with mock.patch(
            "dns.resolver.resolve",
            side_effect=dns.resolver.NoNameservers("SERVFAIL from 10.0.0.1#53"),
        ):
            _, message = verify_domain_ownership(self.domain)
        for leak in ("SERVFAIL", "10.0.0.1", "NoNameservers", "Traceback"):
            self.assertNotIn(leak, message)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class ExclusiveOwnershipTest(TestCase):
    """Only one tenant may hold a verified claim on a name — enforced by the DB."""

    def setUp(self):
        cache.clear()
        self.a_owner = make_user("a@example.test")
        self.tenant_a = make_tenant(self.a_owner, name="A", slug="excl-a")
        self.b_owner = make_user("b@example.test")
        self.tenant_b = make_tenant(self.b_owner, name="B", slug="excl-b")

    def test_two_tenants_may_hold_pending_claims(self):
        """Otherwise a squatter typing a name first would block the real owner."""
        make_unverified_domain(self.tenant_a, "contested.example")
        make_unverified_domain(self.tenant_b, "contested.example")
        self.assertEqual(Domain.objects.filter(domain="contested.example").count(), 2)

    def test_one_tenant_cannot_duplicate_its_own_claim(self):
        make_unverified_domain(self.tenant_a, "dup.example")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_unverified_domain(self.tenant_a, "dup.example")

    def test_database_rejects_a_second_verified_owner(self):
        """The partial unique index, proven directly."""
        make_domain(self.tenant_a, "exclusive.example")  # verified
        pending_b = make_unverified_domain(self.tenant_b, "exclusive.example")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Domain.objects.filter(pk=pending_b.pk).update(
                    ownership_status=DomainOwnership.VERIFIED
                )

    def test_second_tenant_verification_is_refused_gracefully(self):
        """The race surfaces as a clear message, not a 500."""
        make_domain(self.tenant_a, "raced.example")
        b = make_unverified_domain(self.tenant_b, "raced.example")
        token = ensure_verification_token(b)

        # Tenant B somehow publishes the right TXT — the DB still refuses.
        with mock.patch("dns.resolver.resolve", return_value=txt_answer(token)):
            verified, message = verify_domain_ownership(b)

        self.assertFalse(verified)
        self.assertIn("another MateMail workspace", message)
        b.refresh_from_db()
        self.assertEqual(b.ownership_status, DomainOwnership.PENDING)

    def test_losing_the_race_leaves_the_winner_intact(self):
        winner = make_domain(self.tenant_a, "winner.example")
        loser = make_unverified_domain(self.tenant_b, "winner.example")
        token = ensure_verification_token(loser)
        with mock.patch("dns.resolver.resolve", return_value=txt_answer(token)):
            verify_domain_ownership(loser)
        winner.refresh_from_db()
        self.assertTrue(winner.is_ownership_verified)
        self.assertEqual(
            Domain.objects.filter(
                domain="winner.example", ownership_status=DomainOwnership.VERIFIED
            ).count(),
            1,
        )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class ProvisioningGateTest(TestCase):
    """Every path to the Mail Engine must refuse an unverified domain."""

    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("gate@example.test")
        self.tenant = make_tenant(self.owner, name="Gate", slug="gate")
        self.client_api = auth_client(self.owner, self.tenant)
        self.unverified = make_unverified_domain(self.tenant, "ungated.example")
        self.verified = make_domain(self.tenant, "gated.example")

    # ── the service-level gate ──────────────────────────────────────────────

    def test_assert_provisionable_raises_for_unverified(self):
        with self.assertRaises(DomainNotVerified):
            assert_provisionable(self.unverified)

    def test_assert_provisionable_passes_for_verified(self):
        assert_provisionable(self.verified)  # must not raise

    # ── GATE 1: creation does not provision ─────────────────────────────────

    def test_creating_a_domain_does_not_queue_provisioning(self):
        with mock.patch("apps.mail_engine.tasks.provision_domain_task.delay") as prov, \
             mock.patch("apps.dnshealth.tasks.check_domain_dns.delay"):
            res = self.client_api.post(
                "/api/domains/", {"domain": "fresh.example"}, format="json"
            )
        self.assertEqual(res.status_code, 201)
        prov.assert_not_called()
        self.assertFalse(res.data["ownership_verified"])

    def test_new_domain_gets_a_token_and_instructions(self):
        with mock.patch("apps.dnshealth.tasks.check_domain_dns.delay"):
            res = self.client_api.post(
                "/api/domains/", {"domain": "instructed.example"}, format="json"
            )
        self.assertEqual(res.data["verification_record_type"], "TXT")
        self.assertEqual(
            res.data["verification_record_name"], "_matemail-verify.instructed.example"
        )
        self.assertTrue(res.data["verification_record_value"].startswith("matemail-verify-"))
        self.assertIn("TXT record", res.data["verification_instructions"])

    # ── GATE 2: manual provision endpoint ───────────────────────────────────

    def test_provision_endpoint_refuses_unverified(self):
        with mock.patch("apps.mail_engine.tasks.provision_domain_task.delay") as prov:
            res = self.client_api.post(f"/api/domains/{self.unverified.id}/provision/")
        self.assertEqual(res.status_code, 409)
        prov.assert_not_called()

    def test_provision_endpoint_allows_verified(self):
        with mock.patch("apps.mail_engine.tasks.provision_domain_task.delay") as prov:
            res = self.client_api.post(f"/api/domains/{self.verified.id}/provision/")
        self.assertEqual(res.status_code, 200)
        prov.assert_called_once()

    # ── GATE 3: the Celery task itself ──────────────────────────────────────

    def test_task_refuses_unverified_even_when_called_directly(self):
        """
        The gate that matters most: a future caller invoking .delay() directly
        must still not reach the adapter.
        """
        from apps.mail_engine import tasks

        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_domain"
        ) as ensure:
            tasks.provision_domain_task(str(self.unverified.id))
        ensure.assert_not_called()
        self.unverified.refresh_from_db()
        self.assertFalse(self.unverified.mail_engine_provisioned)
        self.assertIn("not been verified", self.unverified.mail_engine_error)

    def test_task_provisions_a_verified_domain(self):
        from apps.mail_engine import tasks

        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_domain"
        ) as ensure:
            tasks.provision_domain_task(str(self.verified.id))
        ensure.assert_called_once()
        self.verified.refresh_from_db()
        self.assertTrue(self.verified.mail_engine_provisioned)

    # ── GATE 4: mailboxes ───────────────────────────────────────────────────

    def test_mailbox_creation_refused_on_unverified_domain(self):
        from apps.mailboxes.models import Mailbox

        before = Mailbox.objects.count()
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox"
        ) as ensure:
            res = self.client_api.post(
                "/api/mailboxes/",
                {
                    "local_part": "alice",
                    "domain_id": str(self.unverified.id),
                    "full_name": "Alice",
                    "password": "Mailbox-Passphrase-9",
                },
                format="json",
            )
        self.assertEqual(res.status_code, 409)
        ensure.assert_not_called()
        self.assertEqual(Mailbox.objects.count(), before)

    def test_mailbox_creation_allowed_on_verified_domain(self):
        res = self.client_api.post(
            "/api/mailboxes/",
            {
                "local_part": "bob",
                "domain_id": str(self.verified.id),
                "full_name": "Bob",
                "password": "Mailbox-Passphrase-9",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201)

    def test_reprovision_refused_when_domain_unverified(self):
        from apps.mailboxes.models import Mailbox

        mailbox = Mailbox.objects.create(
            tenant=self.tenant, domain=self.unverified, local_part="carol", full_name="Carol"
        )
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox"
        ) as ensure:
            res = self.client_api.post(
                f"/api/mailboxes/{mailbox.id}/reprovision/",
                {"password": "Mailbox-Passphrase-9"},
                format="json",
            )
        self.assertEqual(res.status_code, 409)
        ensure.assert_not_called()

    # ── the whole journey ───────────────────────────────────────────────────

    def test_end_to_end_verify_then_provision(self):
        token = self.unverified.verification_token

        # Refused first.
        self.assertEqual(
            self.client_api.post(f"/api/domains/{self.unverified.id}/provision/").status_code,
            409,
        )

        # Publish the record and verify.
        with mock.patch("dns.resolver.resolve", return_value=txt_answer(token)):
            res = self.client_api.post(
                f"/api/domains/{self.unverified.id}/verify-ownership/"
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["verified"])

        # Now provisioning is allowed.
        with mock.patch("apps.mail_engine.tasks.provision_domain_task.delay") as prov:
            res = self.client_api.post(f"/api/domains/{self.unverified.id}/provision/")
        self.assertEqual(res.status_code, 200)
        prov.assert_called_once()


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class VerificationApiTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("api@example.test")
        self.tenant = make_tenant(self.owner, name="Api", slug="api")
        self.client_api = auth_client(self.owner, self.tenant)
        self.domain = make_unverified_domain(self.tenant, "apicheck.example")

    def test_verify_endpoint_returns_409_on_failure(self):
        with mock.patch("dns.resolver.resolve", return_value=txt_answer("wrong")):
            res = self.client_api.post(f"/api/domains/{self.domain.id}/verify-ownership/")
        self.assertEqual(res.status_code, 409)
        self.assertFalse(res.data["verified"])

    def test_verify_writes_an_audit_record(self):
        from apps.logs.models import LogEventType, MailLog

        with mock.patch("dns.resolver.resolve", return_value=txt_answer(self.domain.verification_token)):
            self.client_api.post(f"/api/domains/{self.domain.id}/verify-ownership/")
        self.assertTrue(
            MailLog.objects.filter(
                tenant=self.tenant, event_type=LogEventType.DOMAIN_OWNERSHIP_VERIFIED
            ).exists()
        )

    def test_failed_verification_is_also_audited(self):
        from apps.logs.models import LogEventType, MailLog

        with mock.patch("dns.resolver.resolve", return_value=txt_answer("wrong")):
            self.client_api.post(f"/api/domains/{self.domain.id}/verify-ownership/")
        self.assertTrue(
            MailLog.objects.filter(
                tenant=self.tenant, event_type=LogEventType.DOMAIN_OWNERSHIP_FAILED
            ).exists()
        )

    def test_rotate_endpoint_changes_the_token(self):
        before = self.domain.verification_token
        res = self.client_api.post(
            f"/api/domains/{self.domain.id}/rotate-verification-token/"
        )
        self.assertEqual(res.status_code, 200)
        self.assertNotEqual(res.data["domain"]["verification_record_value"], before)

    def test_another_tenant_cannot_read_or_verify_this_domain(self):
        other = make_user("other@example.test")
        other_tenant = make_tenant(other, name="Other", slug="other-t")
        client = auth_client(other, other_tenant)
        self.assertEqual(
            client.post(f"/api/domains/{self.domain.id}/verify-ownership/").status_code, 404
        )
        self.assertEqual(
            client.get(f"/api/domains/{self.domain.id}/").status_code, 404
        )

    def test_token_is_not_exposed_to_another_tenant_via_list(self):
        other = make_user("other2@example.test")
        other_tenant = make_tenant(other, name="Other2", slug="other-t2")
        res = auth_client(other, other_tenant).get("/api/domains/")
        self.assertNotIn(self.domain.verification_token, res.content.decode())
