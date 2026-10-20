"""Phase 1 regression: per-admin policy remains inert until gated.

No test in this file issues an emergency-reset token or changes real users.
"""
from django.test import TestCase, override_settings

from apps.tenants.admin_hub_mfa_policy import (
    configured_admin_hub_mfa_policy,
    effective_admin_hub_mfa_policy,
    owner_can_manage_admin_hub_mfa,
)
from apps.tenants.models import (
    AdminHubMfaMode, MemberRole, MemberStatus, TenantMembership,
)
from apps.tenants.security_policy import effective_two_factor_policy
from tests.factories import add_member, make_tenant, make_user


@override_settings(
    HUB_ADMIN_MFA_POLICY_ENABLED=False,
    HUB_ADMIN_MFA_ENFORCEMENT_READY=False,
    ORGANIZATION_2FA_POLICY_ENABLED=False,
)
class AdminHubMfaFoundationTest(TestCase):
    def setUp(self):
        self.owner = make_user("owner-mfa@example.test")
        self.tenant = make_tenant(self.owner, "Hub Admin", "hub-admin")
        self.owner_member = TenantMembership.objects.get(tenant=self.tenant, user=self.owner)
        self.admin = make_user("admin-mfa@example.test")
        self.admin_member = add_member(self.tenant, self.admin, MemberRole.ADMIN)
        self.support = make_user("support-mfa@example.test")
        self.support_member = add_member(self.tenant, self.support, MemberRole.SUPPORT)
        self.other_owner = make_user("other-owner-mfa@example.test")
        self.other_tenant = make_tenant(self.other_owner, "Other", "other-mfa")
        self.other_admin = make_user("other-admin-mfa@example.test")
        self.other_member = add_member(self.other_tenant, self.other_admin, MemberRole.ADMIN)

    def test_migration_default_is_non_enforcing(self):
        self.assertEqual(self.admin_member.admin_hub_mfa_policy, AdminHubMfaMode.OFF)
        self.assertEqual(effective_admin_hub_mfa_policy(self.admin_member), AdminHubMfaMode.OFF)
        self.assertEqual(effective_admin_hub_mfa_policy(self.owner_member), AdminHubMfaMode.OFF)

    def test_owner_has_protected_configured_policy_but_no_implicit_rollout(self):
        self.assertEqual(configured_admin_hub_mfa_policy(self.owner_member), AdminHubMfaMode.REQUIRED)
        with override_settings(HUB_ADMIN_MFA_POLICY_ENABLED=True):
            self.assertEqual(effective_admin_hub_mfa_policy(self.owner_member), AdminHubMfaMode.OFF)
        with override_settings(
            HUB_ADMIN_MFA_POLICY_ENABLED=True,
            HUB_ADMIN_MFA_ENFORCEMENT_READY=True,
        ):
            self.assertEqual(effective_admin_hub_mfa_policy(self.owner_member), AdminHubMfaMode.REQUIRED)

    def test_admin_optional_mode_remains_separate_from_mandatory_gate(self):
        self.admin_member.admin_hub_mfa_policy = AdminHubMfaMode.OPTIONAL
        self.admin_member.save(update_fields=["admin_hub_mfa_policy"])
        with override_settings(HUB_ADMIN_MFA_POLICY_ENABLED=True):
            self.assertEqual(effective_admin_hub_mfa_policy(self.admin_member), AdminHubMfaMode.OPTIONAL)

    def test_admin_required_never_enforces_before_ready(self):
        self.admin_member.admin_hub_mfa_policy = AdminHubMfaMode.REQUIRED
        with override_settings(HUB_ADMIN_MFA_POLICY_ENABLED=True):
            self.assertEqual(effective_admin_hub_mfa_policy(self.admin_member), AdminHubMfaMode.OFF)
        with override_settings(
            HUB_ADMIN_MFA_POLICY_ENABLED=True, HUB_ADMIN_MFA_ENFORCEMENT_READY=True,
        ):
            self.assertEqual(effective_admin_hub_mfa_policy(self.admin_member), AdminHubMfaMode.REQUIRED)

    def test_non_admin_and_removed_members_cannot_inherit_a_policy(self):
        self.support_member.admin_hub_mfa_policy = AdminHubMfaMode.REQUIRED
        self.admin_member.status = MemberStatus.REMOVED
        self.admin_member.admin_hub_mfa_policy = AdminHubMfaMode.REQUIRED
        with override_settings(
            HUB_ADMIN_MFA_POLICY_ENABLED=True, HUB_ADMIN_MFA_ENFORCEMENT_READY=True,
        ):
            self.assertEqual(effective_admin_hub_mfa_policy(self.support_member), AdminHubMfaMode.OFF)
            self.assertEqual(effective_admin_hub_mfa_policy(self.admin_member), AdminHubMfaMode.OFF)

    def test_only_real_owner_can_manage_same_tenant_active_admin(self):
        self.assertTrue(owner_can_manage_admin_hub_mfa(self.owner_member, self.admin_member))
        self.assertFalse(owner_can_manage_admin_hub_mfa(self.admin_member, self.owner_member))
        self.assertFalse(owner_can_manage_admin_hub_mfa(self.admin_member, self.other_member))
        self.assertFalse(owner_can_manage_admin_hub_mfa(self.owner_member, self.owner_member))
        self.assertFalse(owner_can_manage_admin_hub_mfa(self.owner_member, self.other_member))
        self.assertFalse(owner_can_manage_admin_hub_mfa(self.support_member, self.admin_member))
        self.admin_member.status = MemberStatus.REMOVED
        self.assertFalse(owner_can_manage_admin_hub_mfa(self.owner_member, self.admin_member))

    def test_forged_owner_role_without_real_tenant_ownership_is_denied(self):
        self.admin_member.role = MemberRole.OWNER
        self.assertFalse(owner_can_manage_admin_hub_mfa(self.admin_member, self.other_member))
        self.assertFalse(owner_can_manage_admin_hub_mfa(self.admin_member, self.owner_member))
        self.assertEqual(configured_admin_hub_mfa_policy(self.admin_member), AdminHubMfaMode.OFF)

    def test_organization_and_postbox_policy_not_implicitly_changed(self):
        self.admin_member.admin_hub_mfa_policy = AdminHubMfaMode.REQUIRED
        self.admin_member.save(update_fields=["admin_hub_mfa_policy"])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.two_factor_policy, "off")
        self.assertEqual(effective_two_factor_policy(self.tenant), "off")
