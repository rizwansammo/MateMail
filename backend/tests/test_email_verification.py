"""
Email verification must gate resource provisioning.

Before Phase 0, signup returned a full token pair immediately and nothing ever
consulted User.email_verified. Combined with unlimited workspace creation, a
scripted signup could mint accounts and provision mail domains and mailboxes
without ever proving control of an address — the classic free-mail spam-farm
entry point, paid for with the platform's own IP reputation.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import EmailVerificationToken
from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "verify-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class EmailVerificationEnforcementTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        # Workspace owner whose address has NOT been verified.
        self.user = make_user("unverified@example.test", verified=False, full_name="Unv")
        self.tenant = make_tenant(self.user, name="Unverified Co", slug="unverified-co")
        self.client_api = auth_client(self.user, self.tenant)

    def _verify(self):
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])

    # ── Blocked while unverified ─────────────────────────────────────────────

    def test_unverified_user_cannot_create_domain(self):
        before = Domain.objects.count()
        res = self.client_api.post(
            "/api/domains/", {"domain": "spam-farm.example"}, format="json"
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Domain.objects.count(), before)

    def test_unverified_user_cannot_create_mailbox(self):
        domain = make_domain(self.tenant, "already.example")
        before = Mailbox.objects.count()
        res = self.client_api.post(
            "/api/mailboxes/",
            {
                "local_part": "bulk",
                "domain_id": str(domain.id),
                "full_name": "Bulk Sender",
                "password": "Mailbox-Passphrase-9",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(Mailbox.objects.count(), before)

    def test_unverified_user_cannot_trigger_domain_provisioning(self):
        domain = make_domain(self.tenant, "provision-me.example")
        res = self.client_api.post(f"/api/domains/{domain.id}/provision/")
        self.assertEqual(res.status_code, 403)

    def test_unverified_user_cannot_reprovision_mailbox(self):
        domain = make_domain(self.tenant, "reprov.example")
        mailbox = Mailbox.objects.create(
            tenant=self.tenant, domain=domain, local_part="rp", full_name="RP"
        )
        res = self.client_api.post(
            f"/api/mailboxes/{mailbox.id}/reprovision/",
            {"password": "Mailbox-Passphrase-9"},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    # ── Reads must still work, so the user can see their workspace ───────────

    def test_unverified_user_can_still_read(self):
        for path in ["/api/domains/", "/api/mailboxes/", "/api/auth/me/", "/api/billing/"]:
            with self.subTest(path=path):
                self.assertEqual(self.client_api.get(path).status_code, 200)

    # ── Unblocked once verified ──────────────────────────────────────────────

    def test_verified_user_can_create_domain(self):
        self._verify()
        res = auth_client(self.user, self.tenant).post(
            "/api/domains/", {"domain": "legitimate.example"}, format="json"
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(Domain.objects.filter(domain="legitimate.example").exists())

    def test_verified_user_can_create_mailbox(self):
        self._verify()
        domain = make_domain(self.tenant, "mailbox-ok.example")
        res = auth_client(self.user, self.tenant).post(
            "/api/mailboxes/",
            {
                "local_part": "alice",
                "domain_id": str(domain.id),
                "full_name": "Alice",
                "password": "Mailbox-Passphrase-9",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201)

    def test_verification_endpoint_flips_the_flag_and_unblocks(self):
        """End to end: redeem the emailed token, then provisioning is permitted."""
        raw, _ = EmailVerificationToken.make(self.user)
        res = self.client_api.post(
            "/api/auth/verify-email/", {"token": raw}, format="json"
        )
        self.assertEqual(res.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

        create = auth_client(self.user, self.tenant).post(
            "/api/domains/", {"domain": "post-verify.example"}, format="json"
        )
        self.assertEqual(create.status_code, 201)
