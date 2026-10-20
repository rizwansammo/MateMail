"""Hub Admin MFA: owner-only policy API and server-side login enforcement."""
import pyotp
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import TwoFactorBackupCode
from apps.accounts.tokens import make_tokens
from apps.logs.models import MailLog
from apps.tenants.models import AdminHubMfaMode, MemberRole
from tests.factories import (
    FAST_PASSWORD_HASHERS, TEST_PASSWORD, add_member, auth_client,
    bearer_client, disable_throttling, enable_2fa, make_tenant, make_user,
)

LOCMEM_CACHE = {"default": {
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "hub-admin-mfa-enrollment-tests",
}}


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM_CACHE,
    HUB_ADMIN_MFA_POLICY_ENABLED=True,
    HUB_ADMIN_MFA_ENFORCEMENT_READY=False,
)
class AdminMfaManagementTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("owner-admin-mfa@example.test")
        self.tenant = make_tenant(self.owner, "Admin Mfa Workspace", "admin-mfa-tenant")
        self.owner_secret = enable_2fa(self.owner)
        self.admin = make_user("another-admin-mfa@example.test")
        self.target = add_member(self.tenant, self.admin, MemberRole.ADMIN)
        self.other_owner = make_user("other-owner-mfa2@example.test")
        self.other_tenant = make_tenant(self.other_owner, "Other Workspace", "admin-mfa-other")
        self.other_admin = make_user("unrelated-mfa@example.test")
        self.other_target = add_member(self.other_tenant, self.other_admin, MemberRole.ADMIN)
        self.url = f"/api/workspaces/{self.tenant.pk}/members/{self.target.pk}/hub-mfa/"
        self.status_url = f"/api/workspaces/{self.tenant.pk}/security/admin-hub-mfa/"

    def data(self, policy="optional", password=TEST_PASSWORD, code=None):
        return {
            "policy": policy,
            "current_password": password,
            "authenticator_code": code or pyotp.TOTP(self.owner_secret).now(),
            "reason": "Owner approved stronger admin security",
        }

    def test_default_feature_guard_refuses_changes_and_hides_control(self):
        with override_settings(HUB_ADMIN_MFA_POLICY_ENABLED=False):
            owner = auth_client(self.owner, self.tenant)
            self.assertEqual(owner.get(self.status_url).data["configurable"], False)
            self.assertEqual(owner.get(self.url).status_code, 503)
            self.assertEqual(owner.patch(self.url, self.data(), format="json").status_code, 503)
            self.target.refresh_from_db()
            self.assertEqual(self.target.admin_hub_mfa_policy, "off")

    def test_owner_sees_only_policy_status_without_credentials(self):
        response = auth_client(self.owner, self.tenant).get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["policy"], "off")
        for secret in ("secret", "totp_secret", "backup_codes", "password_hash", "token"):
            self.assertNotIn(secret, response.data)

    def test_owner_requires_password_and_current_totp(self):
        owner = auth_client(self.owner, self.tenant)
        self.assertEqual(owner.patch(self.url, self.data(password="wrong"), format="json").status_code, 403)
        self.assertEqual(owner.patch(self.url, self.data(code="000000"), format="json").status_code, 403)
        self.target.refresh_from_db()
        self.assertEqual(self.target.admin_hub_mfa_policy, "off")

    def test_owner_can_set_optional_and_change_is_audited(self):
        owner = auth_client(self.owner, self.tenant)
        response = owner.patch(self.url, self.data(), format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.target.refresh_from_db()
        self.assertEqual(self.target.admin_hub_mfa_policy, "optional")
        log = MailLog.objects.get(tenant=self.tenant, event_type="hub_admin_mfa_policy_changed")
        self.assertEqual(log.metadata["target_user_id"], str(self.admin.pk))
        self.assertEqual(log.metadata["old_policy"], "off")
        self.assertEqual(log.metadata["new_policy"], "optional")

    def test_totp_replay_refused_for_second_policy_change(self):
        owner = auth_client(self.owner, self.tenant)
        payload = self.data()
        self.assertEqual(owner.patch(self.url, payload, format="json").status_code, 200)
        payload["policy"] = "off"
        self.assertEqual(owner.patch(self.url, payload, format="json").status_code, 403)
        self.target.refresh_from_db()
        self.assertEqual(self.target.admin_hub_mfa_policy, "optional")

    def test_required_is_locked_until_server_ready(self):
        response = auth_client(self.owner, self.tenant).patch(
            self.url, self.data(policy="required"), format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.target.refresh_from_db()
        self.assertEqual(self.target.admin_hub_mfa_policy, "off")

    def test_peer_admin_and_cross_tenant_requests_denied(self):
        self.assertEqual(auth_client(self.admin, self.tenant).get(self.url).status_code, 404)
        self.assertEqual(auth_client(self.admin, self.tenant).patch(
            self.url, self.data(), format="json",
        ).status_code, 404)
        self.assertEqual(auth_client(self.other_owner, self.other_tenant).get(self.url).status_code, 404)
        cross_target = f"/api/workspaces/{self.tenant.pk}/members/{self.other_target.pk}/hub-mfa/"
        self.assertEqual(auth_client(self.owner, self.tenant).get(cross_target).status_code, 404)

    def test_owner_and_support_are_not_manageable_targets(self):
        member = self.tenant.memberships.get(user=self.owner)
        owner_url = f"/api/workspaces/{self.tenant.pk}/members/{member.pk}/hub-mfa/"
        self.assertEqual(auth_client(self.owner, self.tenant).get(owner_url).status_code, 404)
        support = make_user("support-admin-mfa@example.test")
        membership = add_member(self.tenant, support, MemberRole.SUPPORT)
        support_url = f"/api/workspaces/{self.tenant.pk}/members/{membership.pk}/hub-mfa/"
        self.assertEqual(auth_client(self.owner, self.tenant).get(support_url).status_code, 404)

    def test_unauthenticated_is_rejected(self):
        self.assertIn(APIClient().get(self.url).status_code, (401, 403))

    def test_unknown_fields_are_not_accepted(self):
        payload = {**self.data(), "target_user_id": str(self.other_admin.pk)}
        self.assertEqual(auth_client(self.owner, self.tenant).patch(
            self.url, payload, format="json",
        ).status_code, 400)


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM_CACHE,
    HUB_ADMIN_MFA_POLICY_ENABLED=True,
    HUB_ADMIN_MFA_ENFORCEMENT_READY=True,
)
class AdminMfaLoginTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("owner-required-admin@example.test")
        self.tenant = make_tenant(self.owner, "Admin Require", "admin-mfa-required")
        self.admin = make_user("admin-required-admin@example.test")
        self.membership = add_member(self.tenant, self.admin, MemberRole.ADMIN)
        self.login_url = "/api/auth/login/"

    def login(self):
        return APIClient().post(self.login_url, {
            "email": self.admin.email, "password": TEST_PASSWORD,
        }, format="json")

    def test_optional_policy_advises_without_blocking_login(self):
        self.membership.admin_hub_mfa_policy = AdminHubMfaMode.OPTIONAL
        self.membership.save(update_fields=["admin_hub_mfa_policy"])
        response = self.login()
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertTrue(response.data["recommend_2fa_setup"])
        self.assertIn("access", response.data)

    def test_required_policy_creates_partial_challenge_and_allows_enrollment_only(self):
        self.membership.admin_hub_mfa_policy = AdminHubMfaMode.REQUIRED
        self.membership.save(update_fields=["admin_hub_mfa_policy"])
        response = self.login()
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertTrue(response.data["requires_2fa"])
        self.assertTrue(response.data["requires_enrollment"])
        self.assertNotIn("access", response.data)
        token = response.data["partial_token"]
        start = APIClient().post("/api/auth/2fa/enroll-required/", {
            "partial_token": token, "action": "begin",
        }, format="json")
        self.assertEqual(start.status_code, 200, getattr(start, "data", None))
        code = pyotp.TOTP(start.data["secret"]).now()
        end = APIClient().post("/api/auth/2fa/enroll-required/", {
            "partial_token": token, "action": "confirm", "code": code,
        }, format="json")
        self.assertEqual(end.status_code, 200, getattr(end, "data", None))
        self.assertEqual(len(end.data["backup_codes"]), 10)
        self.assertEqual(TwoFactorBackupCode.objects.filter(user=self.admin).count(), 10)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.two_factor_enabled)

    def test_token_issuer_refuses_unverified_required_admin(self):
        self.membership.admin_hub_mfa_policy = AdminHubMfaMode.REQUIRED
        self.membership.save(update_fields=["admin_hub_mfa_policy"])
        with self.assertRaises(ValueError):
            make_tokens(self.admin, tenant_id=self.tenant.pk)

    def test_old_unverified_access_token_is_rejected_when_policy_changes(self):
        tokens = make_tokens(self.admin, tenant_id=self.tenant.pk)
        self.membership.admin_hub_mfa_policy = AdminHubMfaMode.REQUIRED
        self.membership.save(update_fields=["admin_hub_mfa_policy"])
        response = bearer_client(tokens["access"]).get("/api/workspaces/")
        self.assertEqual(response.status_code, 403)

    def test_owner_is_protected_by_required_policy_when_released(self):
        response = APIClient().post(self.login_url, {
            "email": self.owner.email, "password": TEST_PASSWORD,
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["requires_enrollment"])

    def test_admin_cannot_disable_enrolled_required_factor(self):
        secret = enable_2fa(self.admin)
        self.membership.admin_hub_mfa_policy = AdminHubMfaMode.REQUIRED
        self.membership.save(update_fields=["admin_hub_mfa_policy"])
        token = make_tokens(self.admin, tenant_id=self.tenant.pk, mfa_verified=True)
        client = bearer_client(token["access"])
        response = client.post("/api/auth/2fa/disable/", {
            "password": TEST_PASSWORD, "code": pyotp.TOTP(secret).now(),
        }, format="json")
        self.assertEqual(response.status_code, 409)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.two_factor_enabled)
