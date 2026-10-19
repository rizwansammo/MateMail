"""Enforced Hub MFA tests; isolated, independent of production feature flags."""
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

    def test_enrollment_challenge_cannot_be_used_as_jwt(self):
        login = self.client.post("/api/auth/login/", {
            "email": self.owner.email, "password": TEST_PASSWORD,
        }, format="json")
        raw = login.data["partial_token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(self.client.get("/api/workspaces/").status_code, 401)

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
