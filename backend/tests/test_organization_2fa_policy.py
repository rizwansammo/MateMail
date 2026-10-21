"""Stage 7A policy foundation: rollout is safe before enforcement exists."""
from django.test import TestCase, override_settings
from unittest import mock
from types import SimpleNamespace

from apps.mail_engine.errors import EngineUnavailable
from apps.mail_engine.native_adapter import NativeMailEngineAdapter
from rest_framework.test import APIClient

from apps.tenants.models import OrganizationTwoFactorMode
from tests.factories import (
    FAST_PASSWORD_HASHERS, TEST_PASSWORD, auth_client, make_tenant, make_user, make_domain,
)


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "phase7a-organization-policy-tests",
    }},
)
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

    def test_persisted_require_remains_enforced_after_feature_flag_rollback(self):
        from apps.tenants.security_policy import effective_two_factor_policy
        self.tenant.two_factor_policy = OrganizationTwoFactorMode.REQUIRE
        self.tenant.save(update_fields=["two_factor_policy"])
        with override_settings(
            ORGANIZATION_2FA_POLICY_ENABLED=False,
            ORGANIZATION_2FA_ENFORCEMENT_READY=False,
            ORGANIZATION_2FA_NATIVE_ENFORCEMENT_ACCEPTED=False,
        ):
            self.assertEqual(
                effective_two_factor_policy(self.tenant),
                OrganizationTwoFactorMode.REQUIRE,
            )

    @override_settings(
        ORGANIZATION_2FA_POLICY_ENABLED=True,
        ORGANIZATION_2FA_ENFORCEMENT_READY=False,
    )
    def test_cannot_relax_an_existing_required_policy_during_native_rollback(self):
        self.tenant.two_factor_policy = OrganizationTwoFactorMode.REQUIRE
        self.tenant.save(update_fields=["two_factor_policy"])
        response = auth_client(self.owner, self.tenant).patch(
            self.url, {"policy": "off", "current_password": TEST_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 503, response.data)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.two_factor_policy, OrganizationTwoFactorMode.REQUIRE)

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

    @override_settings(
        ORGANIZATION_2FA_POLICY_ENABLED=True,
        ORGANIZATION_2FA_ENFORCEMENT_READY=True,
        POSTBOX_2FA_ENABLED=True,
        SMTP_IMAP_SECURITY_ENABLED=True,
    )
    def test_require_commits_only_after_native_protocol_enforcement(self):
        domain = make_domain(self.tenant, "primary.example.test")
        adapter = mock.Mock(spec=NativeMailEngineAdapter)
        adapter.list_domains.return_value = [SimpleNamespace(name=domain.domain)]
        with mock.patch("apps.tenants.security_views.get_adapter", return_value=adapter), \
             mock.patch("apps.tenants.security_views.can_activate_require", return_value=True):
            response = auth_client(self.owner, self.tenant).patch(
                self.url, {"policy": "require", "current_password": TEST_PASSWORD},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        adapter.set_tenant_mfa_required.assert_called_once_with(
            str(self.tenant.pk), [domain.domain], True,
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.two_factor_policy, "require")

    @override_settings(
        ORGANIZATION_2FA_POLICY_ENABLED=True,
        ORGANIZATION_2FA_ENFORCEMENT_READY=True,
        POSTBOX_2FA_ENABLED=True,
        SMTP_IMAP_SECURITY_ENABLED=True,
    )
    def test_native_enforcement_failure_does_not_save_require(self):
        adapter = mock.Mock(spec=NativeMailEngineAdapter)
        adapter.list_domains.return_value = []
        adapter.set_tenant_mfa_required.side_effect = EngineUnavailable("synthetic outage")
        with mock.patch("apps.tenants.security_views.get_adapter", return_value=adapter), \
             mock.patch("apps.tenants.security_views.can_activate_require", return_value=True):
            response = auth_client(self.owner, self.tenant).patch(
                self.url, {"policy": "require", "current_password": TEST_PASSWORD},
                format="json",
            )
        self.assertEqual(response.status_code, 503, response.data)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.two_factor_policy, "off")

    @override_settings(
        ORGANIZATION_2FA_POLICY_ENABLED=True,
        ORGANIZATION_2FA_ENFORCEMENT_READY=True,
    )
    def test_native_require_release_retries_even_if_django_already_off(self):
        # Model may already be Off after a failed Native relaxation, so a
        # second identical Off update must attempt Native reconciliation.
        adapter = mock.Mock(spec=NativeMailEngineAdapter)
        adapter.list_domains.return_value = []
        with mock.patch("apps.tenants.security_views.get_adapter", return_value=adapter):
            response = auth_client(self.owner, self.tenant).patch(
                self.url, {"policy": "off", "current_password": TEST_PASSWORD},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        adapter.set_tenant_mfa_required.assert_called_once_with(
            str(self.tenant.pk), [], False,
        )
