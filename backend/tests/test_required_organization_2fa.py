"""Enforced Hub MFA tests; isolated, independent of production feature flags."""
import uuid

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
import pyotp

from apps.accounts.models import TwoFactorSetup
from tests.factories import (
    FAST_PASSWORD_HASHERS, TEST_PASSWORD, auth_client, make_tenant, make_user,
)

LOCMEM = {"default": {
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "phase7a-org-require",
}}


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM,
    ORGANIZATION_2FA_POLICY_ENABLED=True,
    ORGANIZATION_2FA_ENFORCEMENT_READY=True,
    POSTBOX_2FA_ENABLED=True,
    SMTP_IMAP_SECURITY_ENABLED=True,
)
class RequiredOrganizationMfaTest(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.owner = make_user("owner-require@example.test")
        self.tenant = make_tenant(self.owner, "Require MFA", "require-mfa")
        self.tenant.two_factor_policy = "require"
        self.tenant.save(update_fields=["two_factor_policy"])
        self.client = APIClient()

    def test_password_cannot_yield_session_before_enrollment(self):
        response = self.client.post("/api/auth/login/", {
            "email": self.owner.email, "password": TEST_PASSWORD,
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["requires_enrollment"])
        self.assertNotIn("access", response.data)

    def test_old_jwt_denied_by_live_tenant_policy(self):
        client = auth_client(self.owner, self.tenant)
        response = client.get("/api/workspaces/")
        self.assertEqual(response.status_code, 403)

    def test_api_key_cannot_bypass_required_interactive_mfa(self):
        from tests.factories import make_api_key, bearer_client
        key, _ = make_api_key(self.tenant, self.owner)
        response = bearer_client(key).get("/api/domains/")
        self.assertEqual(response.status_code, 403)
        self.assertIn("2FA", response.json()["detail"])

    def test_require_rejects_pre_mfa_refresh_cookie(self):
        from apps.accounts.tokens import make_tokens
        tokens = make_tokens(self.owner, tenant_id=self.tenant.pk)
        self.client.cookies["matemail_refresh"] = tokens["refresh"]
        response = self.client.post("/api/auth/refresh/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.cookies["matemail_refresh"].value, "")

    def test_refresh_rejects_missing_tenant_in_existing_cookie(self):
        from apps.accounts.tokens import make_tokens
        tokens = make_tokens(self.owner, tenant_id=uuid.uuid4())
        self.client.cookies["matemail_refresh"] = tokens["refresh"]
        response = self.client.post("/api/auth/refresh/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.cookies["matemail_refresh"].value, "")

    def test_refresh_refuses_removed_organization_membership(self):
        from apps.accounts.tokens import make_tokens
        from apps.tenants.models import TenantMembership, MemberStatus
        from tests.factories import enable_2fa
        enable_2fa(self.owner)
        tokens = make_tokens(self.owner, tenant_id=self.tenant.pk, mfa_verified=True)
        TenantMembership.objects.filter(tenant=self.tenant, user=self.owner).update(
            status=MemberStatus.REMOVED,
        )
        self.client.cookies["matemail_refresh"] = tokens["refresh"]
        response = self.client.post("/api/auth/refresh/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.cookies["matemail_refresh"].value, "")
        # Restoring a membership must not resurrect an already revoked cookie.
        TenantMembership.objects.filter(tenant=self.tenant, user=self.owner).update(
            status=MemberStatus.ACTIVE,
        )
        self.client.cookies["matemail_refresh"] = tokens["refresh"]
        self.assertEqual(self.client.post("/api/auth/refresh/").status_code, 401)

    def test_verified_mfa_refresh_rotates_and_preserves_proof(self):
        from apps.accounts.tokens import make_tokens
        from tests.factories import enable_2fa
        enable_2fa(self.owner)
        tokens = make_tokens(self.owner, tenant_id=self.tenant.pk, mfa_verified=True)
        self.client.cookies["matemail_refresh"] = tokens["refresh"]
        rotated = self.client.post("/api/auth/refresh/")
        self.assertEqual(rotated.status_code, 200)
        proof = APIClient()
        proof.credentials(HTTP_AUTHORIZATION=f"Bearer {rotated.data['access']}")
        self.assertEqual(proof.get("/api/workspaces/").status_code, 200)
        self.client.cookies["matemail_refresh"] = tokens["refresh"]
        self.assertEqual(self.client.post("/api/auth/refresh/").status_code, 401)

    def test_enrollment_challenge_cannot_be_used_as_jwt(self):
        login = self.client.post("/api/auth/login/", {
            "email": self.owner.email, "password": TEST_PASSWORD,
        }, format="json")
        raw = login.data["partial_token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(self.client.get("/api/workspaces/").status_code, 401)

    def test_2fa_challenge_cannot_mint_session_after_membership_revoked(self):
        from apps.accounts.challenge import peek_challenge
        from apps.tenants.models import TenantMembership, MemberStatus
        from tests.factories import enable_2fa

        secret = enable_2fa(self.owner)
        login = self.client.post("/api/auth/login/", {
            "email": self.owner.email, "password": TEST_PASSWORD,
        }, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        token = login.data["partial_token"]
        self.assertIsNotNone(peek_challenge(token))
        TenantMembership.objects.filter(
            tenant=self.tenant, user=self.owner,
        ).update(status=MemberStatus.REMOVED)
        code = pyotp.TOTP(secret).now()
        response = self.client.post("/api/auth/2fa/verify/", {
            "partial_token": token, "code": code,
        }, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertNotIn("access", response.data)
        self.assertNotIn("matemail_refresh", response.cookies)
        self.assertIsNone(peek_challenge(token))

    def test_2fa_removed_member_cannot_consume_backup_code(self):
        from apps.accounts.challenge import peek_challenge
        from apps.tenants.models import TenantMembership, MemberStatus
        from tests.factories import enable_2fa
        from apps.accounts.models import TwoFactorBackupCode
        from apps.accounts.tokens import hash_token

        enable_2fa(self.owner)
        backup = "ABCD1234"
        TwoFactorBackupCode.objects.create(
            user=self.owner, code_hash=hash_token(backup),
        )
        login = self.client.post("/api/auth/login/", {
            "email": self.owner.email, "password": TEST_PASSWORD,
        }, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        partial = login.data["partial_token"]
        TenantMembership.objects.filter(
            tenant=self.tenant, user=self.owner,
        ).update(status=MemberStatus.REMOVED)
        response = self.client.post("/api/auth/2fa/verify/", {
            "partial_token": partial, "code": backup,
        }, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertNotIn("access", response.data)
        self.assertIsNone(peek_challenge(partial))
        self.assertFalse(TwoFactorBackupCode.objects.get(
            user=self.owner, code_hash=hash_token(backup),
        ).is_used)

    def test_required_enrollment_then_verified_session(self):
        login = self.client.post("/api/auth/login/", {
            "email": self.owner.email, "password": TEST_PASSWORD,
        }, format="json")
        partial = login.data["partial_token"]
        begin = self.client.post("/api/auth/2fa/enroll-required/", {
            "partial_token": partial, "action": "begin",
        }, format="json")
        self.assertEqual(begin.status_code, 200, begin.data)
        setup = TwoFactorSetup.objects.get(user=self.owner)
        code = pyotp.TOTP(__import__("apps.accounts.totp_crypto", fromlist=["decrypt_secret"]).decrypt_secret(setup.totp_secret)).now()
        finish = self.client.post("/api/auth/2fa/enroll-required/", {
            "partial_token": partial, "action": "confirm", "code": code,
        }, format="json")
        self.assertEqual(finish.status_code, 200, finish.data)
        self.assertIn("access", finish.data)
        self.assertEqual(len(finish.data["backup_codes"]), 10)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.two_factor_enabled)
        authenticated = APIClient()
        authenticated.credentials(HTTP_AUTHORIZATION=f"Bearer {finish.data['access']}")
        self.assertEqual(authenticated.get("/api/workspaces/").status_code, 200)
        denied = authenticated.post("/api/auth/2fa/disable/", {
            "password": TEST_PASSWORD,
        }, format="json")
        self.assertEqual(denied.status_code, 409)
        replay = self.client.post("/api/auth/2fa/enroll-required/", {
            "partial_token": partial, "action": "confirm", "code": code,
        }, format="json")
        self.assertEqual(replay.status_code, 401)
