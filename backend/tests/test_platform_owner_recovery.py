"""
Primary Owner recovery, and the boundaries around it.

Two properties matter more than the feature itself:

  1. The operator starts recovery; only the owner's mailbox finishes it. The
     reset token never appears in an API response, so a platform administrator
     cannot take the account over — they can only ask its owner to.

  2. The target cannot be aimed. It is `tenant.owner`, derived from the URL, so
     there is no field to tamper with and no way to reach another
     organization's owner, an ordinary member, or another administrator.
"""
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import PasswordResetToken
from apps.platform_admin.models import PlatformAuditLog
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_tenant,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "platform-owner-tests",
    }
}


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM_CACHE,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="mx.matemail.test",
)
class OwnerRecoveryTest(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        disable_throttling(self)

        self.admin = make_user("ops@netamate.test", is_platform_admin=True)
        self.owner = make_user("owner@acme.test", full_name="Ada Owner")
        self.other_owner = make_user("owner@globex.test")
        self.member = make_user("member@acme.test")

        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        self.other_tenant = make_tenant(self.other_owner, name="Globex", slug="globex")

        self.client_admin = auth_client(self.admin)

    def owner_url(self, tenant=None, suffix=""):
        return f"/api/platform/tenants/{(tenant or self.tenant).id}/owner/{suffix}"

    def recover(self, tenant=None, reason="Owner locked out, verified by phone"):
        return self.client_admin.post(
            self.owner_url(tenant, "password-recovery/"),
            {"reason": reason},
            format="json",
        )

    # ── metadata ────────────────────────────────────────────────────────────

    def test_the_console_shows_the_owner(self):
        response = self.client_admin.get(self.owner_url())
        self.assertEqual(200, response.status_code)
        self.assertEqual(self.owner.email, response.data["owner"]["email"])
        self.assertEqual("Ada Owner", response.data["owner"]["full_name"])

    def test_no_secret_is_exposed_in_the_owner_payload(self):
        payload = self.client_admin.get(self.owner_url()).data["owner"]
        for forbidden in ("password", "totp_secret", "token", "secret", "backup_codes"):
            self.assertNotIn(forbidden, payload)

    # ── recovery ────────────────────────────────────────────────────────────

    def test_recovery_emails_the_owner_and_nobody_else(self):
        response = self.recover()
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(mail.outbox))
        self.assertEqual([self.owner.email], mail.outbox[0].to)

    def test_recovery_creates_a_real_reset_token_for_the_owner(self):
        self.recover()
        self.assertTrue(PasswordResetToken.objects.filter(user=self.owner).exists())

    def test_the_platform_admin_never_receives_the_token(self):
        """
        The line between "start recovery" and "take the account over".
        """
        response = self.recover()

        token = PasswordResetToken.objects.get(user=self.owner)
        body = str(response.data)
        self.assertNotIn(token.token_hash, body)
        for key in ("token", "reset_url", "raw", "link"):
            self.assertNotIn(key, response.data)

    def test_recovery_does_not_change_the_password(self):
        self.recover()
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("Test-Passphrase-42"))

    def test_recovery_requires_a_reason(self):
        response = self.client_admin.post(
            self.owner_url(suffix="password-recovery/"), {}, format="json"
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("reason", response.data)

    def test_recovery_is_audited_with_actor_tenant_and_target(self):
        self.recover(reason="Ticket 4182")

        row = PlatformAuditLog.objects.get(action="owner.password_recovery")
        self.assertEqual(self.admin, row.actor)
        self.assertEqual(self.tenant, row.tenant)
        self.assertEqual(self.owner, row.target_user)
        self.assertEqual("Ticket 4182", row.reason)

    def test_the_audit_row_carries_no_token(self):
        self.recover()
        row = PlatformAuditLog.objects.get(action="owner.password_recovery")
        token = PasswordResetToken.objects.get(user=self.owner)
        self.assertNotIn(token.token_hash, str(row.metadata))

    # ── the target cannot be aimed ──────────────────────────────────────────

    def test_recovery_hits_the_owner_not_another_member(self):
        """
        An organization can have several admins; only one created it. Passing a
        user id must not redirect the action — there is no such field.
        """
        self.client_admin.post(
            self.owner_url(suffix="password-recovery/"),
            {"reason": "test", "user_id": str(self.member.id), "email": self.member.email},
            format="json",
        )
        self.assertEqual([self.owner.email], mail.outbox[0].to)
        self.assertFalse(PasswordResetToken.objects.filter(user=self.member).exists())

    def test_one_tenants_recovery_cannot_reach_another_tenants_owner(self):
        self.recover(tenant=self.tenant)
        self.assertFalse(
            PasswordResetToken.objects.filter(user=self.other_owner).exists()
        )

    def test_an_unknown_tenant_is_404(self):
        import uuid

        response = self.client_admin.post(
            f"/api/platform/tenants/{uuid.uuid4()}/owner/password-recovery/",
            {"reason": "test"},
            format="json",
        )
        self.assertEqual(404, response.status_code)

    # ── session revocation ──────────────────────────────────────────────────

    def test_revoking_sessions_affects_only_the_owner(self):
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )
        from apps.accounts.tokens import make_tokens

        make_tokens(self.owner)
        make_tokens(self.other_owner)

        response = self.client_admin.post(
            self.owner_url(suffix="revoke-sessions/"),
            {"reason": "Compromise reported"},
            format="json",
        )
        self.assertEqual(200, response.status_code)

        blacklisted_users = {
            row.token.user_id for row in BlacklistedToken.objects.select_related("token")
        }
        self.assertIn(self.owner.id, blacklisted_users)
        self.assertNotIn(self.other_owner.id, blacklisted_users)

    def test_session_revocation_is_audited(self):
        self.client_admin.post(
            self.owner_url(suffix="revoke-sessions/"),
            {"reason": "Compromise reported"},
            format="json",
        )
        row = PlatformAuditLog.objects.get(action="owner.revoke_sessions")
        self.assertEqual(self.owner, row.target_user)
        self.assertEqual(self.tenant, row.tenant)

    # ── authorization ───────────────────────────────────────────────────────

    def test_a_tenant_user_cannot_trigger_recovery(self):
        for client in (auth_client(self.owner, self.tenant), auth_client(self.member)):
            response = client.post(
                self.owner_url(suffix="password-recovery/"),
                {"reason": "let me in"},
                format="json",
            )
            self.assertIn(response.status_code, (401, 403))
        self.assertEqual([], mail.outbox)

    def test_an_anonymous_caller_cannot_trigger_recovery(self):
        response = APIClient().post(
            self.owner_url(suffix="password-recovery/"), {"reason": "x"}, format="json"
        )
        self.assertIn(response.status_code, (401, 403))

    def test_a_tenant_user_cannot_read_the_owner_panel(self):
        response = auth_client(self.owner, self.tenant).get(self.owner_url())
        self.assertIn(response.status_code, (401, 403))

    def test_no_mailbox_content_is_reachable_through_recovery(self):
        """
        The boundary stated as a test: recovery returns account metadata and a
        delivery result, never anything from the owner's mail.
        """
        payload = self.client_admin.get(self.owner_url()).data["owner"]
        for forbidden in ("messages", "mailbox", "inbox", "mail", "quota_used"):
            self.assertNotIn(forbidden, payload)
