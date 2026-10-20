"""Phase 3: recipient-confirmed, owner-authorized Hub Admin 2FA recovery.

All credentials and emails here are synthetic and isolated to the test DB.
"""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import (
    TwoFactorBackupCode, TwoFactorSetup, VerifiedRecoveryEmail,
)
from apps.accounts.tokens import hash_token, make_tokens
from apps.logs.models import MailLog
from apps.tenants.models import AdminHubMfaRecoveryTicket, MemberRole, MemberStatus
from tests.factories import (
    FAST_PASSWORD_HASHERS, TEST_PASSWORD, add_member, auth_client, bearer_client,
    disable_throttling, enable_2fa, make_api_key, make_tenant, make_user,
)

CACHE = {"default": {
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "admin-mfa-recovery-phase3-tests",
}}


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=CACHE,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="mx.matemail.test",
    FRONTEND_URL="https://hub.matemail.test",
    HUB_ADMIN_MFA_POLICY_ENABLED=True,
    HUB_ADMIN_MFA_ENFORCEMENT_READY=False,
    HUB_ADMIN_MFA_RECOVERY_ENABLED=True,
)
class AdminMfaRecoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        disable_throttling(self)
        self.owner = make_user("security-owner@example.test")
        self.tenant = make_tenant(self.owner, "Security", "recovery-a")
        self.owner_secret = enable_2fa(self.owner)
        self.admin = make_user("admin@example.test")
        self.member = add_member(self.tenant, self.admin, MemberRole.ADMIN)
        self.admin_secret = enable_2fa(self.admin)
        self.contact = VerifiedRecoveryEmail.objects.create(
            user=self.admin,
            email="private-recovery@outside.test",
            verified_at=timezone.now(),
        )
        self.old_code = "ABC123XY"
        TwoFactorBackupCode.objects.create(
            user=self.admin, code_hash=hash_token(self.old_code),
        )
        self.owner_client = auth_client(self.owner, self.tenant)
        self.admin_client = auth_client(self.admin, self.tenant)
        self.request_url = (
            f"/api/workspaces/{self.tenant.pk}/members/{self.member.pk}/hub-mfa/recovery/"
        )
        self.confirm_url = "/api/auth/2fa/admin-recovery/confirm/"

    def request_payload(self, **overrides):
        return {
            "current_password": TEST_PASSWORD,
            "authenticator_code": __import__("pyotp").TOTP(self.owner_secret).now(),
            "reason": "Administrator lost authenticator and backup access",
            **overrides,
        }

    def start(self, client=None, **kwargs):
        return (client or self.owner_client).post(
            self.request_url, self.request_payload(**kwargs), format="json",
        )

    def approval_token(self):
        self.assertTrue(mail.outbox)
        self.assertEqual(mail.outbox[0].to, [self.contact.email])
        match = re.search(r"recover-admin#token=([A-Za-z0-9_-]+)", mail.outbox[0].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def complete(self, token):
        return APIClient().post(self.confirm_url, {
            "token": token, "confirm": True,
        }, format="json")

    def test_recovery_disabled_by_default(self):
        with override_settings(HUB_ADMIN_MFA_RECOVERY_ENABLED=False):
            result = self.start()
            self.assertEqual(result.status_code, 503)
            self.assertFalse(AdminHubMfaRecoveryTicket.objects.exists())
            self.assertTrue(self.admin.two_factor_enabled)

    def test_only_real_owner_can_request_admin_recovery(self):
        self.assertEqual(self.start(client=self.admin_client).status_code, 404)
        another_owner = make_user("another-owner@example.test")
        another_tenant = make_tenant(another_owner, "Other", "recovery-other")
        self.assertEqual(self.start(client=auth_client(another_owner, another_tenant)).status_code, 404)
        self.assertEqual(APIClient().post(
            self.request_url, self.request_payload(), format="json",
        ).status_code, 401)
        owner_member = self.tenant.memberships.get(user=self.owner)
        owner_url = f"/api/workspaces/{self.tenant.pk}/members/{owner_member.pk}/hub-mfa/recovery/"
        self.assertEqual(self.owner_client.post(owner_url, self.request_payload(), format="json").status_code, 404)
        self.assertFalse(AdminHubMfaRecoveryTicket.objects.exists())

    def test_invalid_owner_password_and_otp_do_not_issue_recovery(self):
        self.assertEqual(self.start(current_password="bad-password").status_code, 403)
        self.assertEqual(self.start(authenticator_code="000000").status_code, 403)
        self.assertFalse(AdminHubMfaRecoveryTicket.objects.exists())

    def test_request_requires_independent_verified_contact(self):
        self.contact.verified_at = None
        self.contact.save(update_fields=["verified_at"])
        self.assertEqual(self.start().status_code, 409)
        self.assertFalse(AdminHubMfaRecoveryTicket.objects.exists())

    def test_unconfigured_admin_is_not_resettable(self):
        self.admin.two_factor_enabled = False
        self.admin.save(update_fields=["two_factor_enabled"])
        self.assertEqual(self.start().status_code, 409)
        self.assertFalse(AdminHubMfaRecoveryTicket.objects.exists())

    def test_request_sends_approval_only_to_external_verified_email(self):
        r = self.start()
        self.assertEqual(r.status_code, 202, getattr(r, "data", None))
        token = self.approval_token()
        self.assertNotIn(token, str(r.data))
        self.assertNotIn(token, str(AdminHubMfaRecoveryTicket.objects.first().nonce_hash))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.two_factor_enabled)
        self.assertTrue(TwoFactorSetup.objects.filter(user=self.admin).exists())
        audit = MailLog.objects.get(tenant=self.tenant, event_type="hub_admin_mfa_recovery_requested")
        self.assertEqual(audit.source, self.owner.email)
        self.assertEqual(audit.metadata["target_user_id"], str(self.admin.pk))
        self.assertNotIn(token, str(audit.metadata))

    def test_failed_email_delivery_cancels_ticket_without_reset(self):
        with mock.patch(
            "apps.tenants.admin_hub_mfa_recovery_views.send_transactional",
            return_value=False,
        ):
            r = self.start()
        self.assertEqual(r.status_code, 503)
        ticket = AdminHubMfaRecoveryTicket.objects.first()
        self.assertIsNotNone(ticket.cancelled_at)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.two_factor_enabled)

    def test_confirmation_resets_factor_codes_sessions_and_api_keys(self):
        tokens = make_tokens(self.admin, tenant_id=self.tenant.pk)
        _raw_api_key, key = make_api_key(self.tenant, self.admin)
        # One person can administer multiple workspaces. A security recovery
        # invalidates their own API keys everywhere, not another user's keys.
        second_owner = make_user("second-tenant-owner@example.test")
        second_tenant = make_tenant(second_owner, "Recovery Other", "recovery-b")
        unrelated_user = make_user("unrelated-key-owner@example.test")
        add_member(second_tenant, self.admin, MemberRole.SUPPORT)
        add_member(second_tenant, unrelated_user, MemberRole.ADMIN)
        _cross_raw, cross_key = make_api_key(second_tenant, self.admin)
        _unrelated_raw, unrelated_key = make_api_key(second_tenant, unrelated_user)
        self.assertEqual(self.start().status_code, 202)
        token = self.approval_token()
        self.assertEqual(
            bearer_client(tokens["access"]).get("/api/workspaces/").status_code, 200
        )
        done = self.complete(token)
        self.assertEqual(done.status_code, 200, getattr(done, "data", None))
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.two_factor_enabled)
        self.assertTrue(self.admin.hub_mfa_reenrollment_required)
        self.assertEqual(self.admin.hub_security_epoch, 1)
        self.assertFalse(TwoFactorSetup.objects.filter(user=self.admin).exists())
        self.assertFalse(TwoFactorBackupCode.objects.filter(user=self.admin).exists())
        key.refresh_from_db()
        cross_key.refresh_from_db()
        unrelated_key.refresh_from_db()
        self.assertFalse(key.is_active)
        self.assertFalse(cross_key.is_active)
        self.assertTrue(unrelated_key.is_active)
        self.assertIn(
            bearer_client(tokens["access"]).get("/api/workspaces/").status_code,
            (401, 403),
        )
        self.assertIn(
            bearer_client(tokens["access"]).get("/api/auth/me/").status_code,
            (401, 403),
        )
        self.assertEqual(self.complete(token).status_code, 401)
        audit = MailLog.objects.get(tenant=self.tenant, event_type="hub_admin_mfa_recovery_completed")
        self.assertEqual(audit.metadata["target_user_id"], str(self.admin.pk))
        self.assertGreaterEqual(audit.metadata["revoked_refresh_tokens"], 1)
        self.assertEqual(audit.metadata["revoked_api_keys"], 2)

    def test_recovery_forces_enrollment_even_when_admin_policy_off(self):
        self.assertEqual(self.member.admin_hub_mfa_policy, "off")
        self.assertEqual(self.start().status_code, 202)
        self.assertEqual(self.complete(self.approval_token()).status_code, 200)
        login = APIClient().post("/api/auth/login/", {
            "email": self.admin.email, "password": TEST_PASSWORD,
        }, format="json")
        self.assertEqual(login.status_code, 200, getattr(login, "data", None))
        self.assertTrue(login.data["requires_2fa"])
        self.assertTrue(login.data["requires_enrollment"])
        self.assertNotIn("access", login.data)
        partial = login.data["partial_token"]
        begin = APIClient().post("/api/auth/2fa/enroll-required/", {
            "action": "begin", "partial_token": partial,
        }, format="json")
        self.assertEqual(begin.status_code, 200, getattr(begin, "data", None))
        code = __import__("pyotp").TOTP(begin.data["secret"]).now()
        confirm = APIClient().post("/api/auth/2fa/enroll-required/", {
            "action": "confirm", "partial_token": partial, "code": code,
        }, format="json")
        self.assertEqual(confirm.status_code, 200, getattr(confirm, "data", None))
        self.assertEqual(len(confirm.data["backup_codes"]), 10)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.two_factor_enabled)
        self.assertFalse(self.admin.hub_mfa_reenrollment_required)
        self.assertEqual(self.admin.hub_security_epoch, 1)

    def test_pre_recovery_partial_login_challenge_is_invalid(self):
        prior = APIClient().post("/api/auth/login/", {
            "email": self.admin.email, "password": TEST_PASSWORD,
        }, format="json")
        self.assertTrue(prior.data["requires_2fa"])
        self.assertEqual(self.start().status_code, 202)
        self.assertEqual(self.complete(self.approval_token()).status_code, 200)
        rejected = APIClient().post("/api/auth/2fa/enroll-required/", {
            "action": "begin", "partial_token": prior.data["partial_token"],
        }, format="json")
        self.assertEqual(rejected.status_code, 401)

    def test_expired_ticket_does_not_change_any_credentials(self):
        self.assertEqual(self.start().status_code, 202)
        token = self.approval_token()
        AdminHubMfaRecoveryTicket.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(self.complete(token).status_code, 401)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.two_factor_enabled)
        self.assertEqual(self.admin.hub_security_epoch, 0)

    def test_wrong_token_and_missing_consent_rejected(self):
        self.assertEqual(self.start().status_code, 202)
        token = self.approval_token()
        self.assertEqual(self.complete("not-a-valid-token").status_code, 400)
        self.assertEqual(APIClient().post(self.confirm_url, {
            "token": token, "confirm": False,
        }, format="json").status_code, 400)
        self.assertTrue(self.admin.two_factor_enabled)

    def test_revoked_or_changed_recovery_email_cancels_authority(self):
        self.assertEqual(self.start().status_code, 202)
        token = self.approval_token()
        self.contact.email = "new-independent@example.test"
        self.contact.save(update_fields=["email"])
        self.assertEqual(self.complete(token).status_code, 409)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.hub_security_epoch, 0)

    def test_demoting_or_removing_admin_before_confirm_blocks_reset(self):
        self.assertEqual(self.start().status_code, 202)
        token = self.approval_token()
        self.member.status = MemberStatus.REMOVED
        self.member.save(update_fields=["status"])
        self.assertEqual(self.complete(token).status_code, 403)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.two_factor_enabled)

    def test_owner_change_before_confirm_blocks_reset(self):
        self.assertEqual(self.start().status_code, 202)
        token = self.approval_token()
        successor = make_user("new-owner@example.test")
        self.tenant.owner = successor
        self.tenant.save(update_fields=["owner"])
        self.assertEqual(self.complete(token).status_code, 403)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.two_factor_enabled)

    def test_second_recovery_request_invalidates_first_link(self):
        self.assertEqual(self.start().status_code, 202)
        first = self.approval_token()
        cache.clear()  # simulate time passing so a new owner OTP can be provided
        mail.outbox.clear()
        self.assertEqual(self.start().status_code, 202)
        second = self.approval_token()
        self.assertNotEqual(first, second)
        self.assertEqual(self.complete(first).status_code, 401)
        self.assertEqual(self.complete(second).status_code, 200)

    def test_other_users_sessions_are_untouched(self):
        unaffected = make_user("not-target@example.test")
        add_member(self.tenant, unaffected, MemberRole.SUPPORT)
        tokens = make_tokens(unaffected, tenant_id=self.tenant.pk)
        self.assertEqual(self.start().status_code, 202)
        self.assertEqual(self.complete(self.approval_token()).status_code, 200)
        self.assertEqual(
            bearer_client(tokens["access"]).get("/api/workspaces/").status_code, 200
        )
