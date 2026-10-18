"""Stage 7A policy foundation: rollout is safe before enforcement exists."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.tenants.models import OrganizationTwoFactorMode
from tests.factories import (
    FAST_PASSWORD_HASHERS, TEST_PASSWORD, auth_client, make_tenant, make_user,
)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class OrganizationTwoFactorPolicyTest(TestCase):
    def setUp(self):
        self.owner = make_user("owner-a@example.test")
        self.tenant = make_tenant(self.owner, "Organization A", "org-a")
        self.other = make_user("owner-b@example.test")
        self.other_tenant = make_tenant(self.other, "Organization B", "org-b")
        self.url = f"/api/workspaces/{self.tenant.pk}/security/2fa/"

    def test_initial_default_off(self):
        self.assertEqual(self.tenant.two_factor_policy, OrganizationTwoFactorMode.OFF)
        response = auth_client(self.owner, self.tenant).get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["effective_policy"], "off")

    def test_anonymous_is_denied(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_tenant_isolation(self):
        client = auth_client(self.other, self.other_tenant)
        self.assertEqual(client.get(self.url).status_code, 404)
        with override_settings(ORGANIZATION_2FA_POLICY_ENABLED=True):
            self.assertEqual(client.patch(self.url, {
                "policy": "encourage", "current_password": TEST_PASSWORD
            }, format="json").status_code, 404)

    def test_feature_flag_protects_mutations(self):
        response = auth_client(self.owner, self.tenant).patch(self.url, {
            "policy": "encourage", "current_password": TEST_PASSWORD
        }, format="json")
        self.assertEqual(response.status_code, 503)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.two_factor_policy, "off")

    @override_settings(
        ORGANIZATION_2FA_POLICY_ENABLED=True,
        ORGANIZATION_2FA_ENFORCEMENT_READY=True,
    )
    def test_require_is_fail_closed_even_if_flags_are_on(self):
        response = auth_client(self.owner, self.tenant).patch(self.url, {
            "policy": "require", "current_password": TEST_PASSWORD
        }, format="json")
        self.assertEqual(response.status_code, 409)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.two_factor_policy, "off")

    @override_settings(ORGANIZATION_2FA_POLICY_ENABLED=True)
    def test_encourage_requires_reauthentication(self):
        client = auth_client(self.owner, self.tenant)
        wrong = client.patch(self.url, {
            "policy": "encourage", "current_password": "bad-password"
        }, format="json")
        self.assertEqual(wrong.status_code, 403)
        success = client.patch(self.url, {
            "policy": "encourage", "current_password": TEST_PASSWORD
        }, format="json")
        self.assertEqual(success.status_code, 200)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.two_factor_policy, "encourage")

    @override_settings(ORGANIZATION_2FA_POLICY_ENABLED=True)
    def test_readonly_member_cannot_change_policy(self):
        from apps.tenants.models import MemberRole, MemberStatus, TenantMembership
        user = make_user("readonly-a@example.test")
        TenantMembership.objects.create(
            user=user, tenant=self.tenant, role=MemberRole.READ_ONLY, status=MemberStatus.ACTIVE
        )
        response = auth_client(user, self.tenant).patch(self.url, {
            "policy": "encourage", "current_password": TEST_PASSWORD
        }, format="json")
        self.assertEqual(response.status_code, 403)

    @override_settings(ORGANIZATION_2FA_POLICY_ENABLED=True)
    def test_unknown_fields_are_rejected(self):
        response = auth_client(self.owner, self.tenant).patch(self.url, {
            "policy": "encourage",
            "current_password": TEST_PASSWORD,
            "enable_without_otp": True,
        }, format="json")
        self.assertEqual(response.status_code, 400)
