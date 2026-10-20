"""Hub administrator policy foundation. Not an authentication endpoint.

The owner is always *configured* as required. Runtime enforcement remains
disabled until both independently controlled Hub-only release gates are ON.
An admin policy must never alter mailbox or Native Engine authentication.
"""
from django.conf import settings

from .models import AdminHubMfaMode, MemberRole, MemberStatus


def configured_admin_hub_mfa_policy(membership):
    """Return a Hub-only policy, never a mailbox or organization-wide policy."""
    if membership is None or membership.status != MemberStatus.ACTIVE:
        return AdminHubMfaMode.OFF
    if membership.role == MemberRole.OWNER:
        # Only the verified tenant owner may carry the protected owner role.
        if membership.tenant.owner_id == membership.user_id:
            return AdminHubMfaMode.REQUIRED
        return AdminHubMfaMode.OFF
    if membership.role == MemberRole.ADMIN:
        return membership.admin_hub_mfa_policy
    return AdminHubMfaMode.OFF


def effective_admin_hub_mfa_policy(membership):
    """Fail closed for rollout: no new login behavior before release approval.

    The REQUIRE authentication implementation must check the full effective
    policy on every issuance/refresh and session, not trust the client UI.
    """
    if not getattr(settings, "HUB_ADMIN_MFA_POLICY_ENABLED", False):
        return AdminHubMfaMode.OFF
    configured = configured_admin_hub_mfa_policy(membership)
    if configured == AdminHubMfaMode.REQUIRED and not getattr(
        settings, "HUB_ADMIN_MFA_ENFORCEMENT_READY", False
    ):
        return AdminHubMfaMode.OFF
    return configured


def owner_can_manage_admin_hub_mfa(actor_membership, target_membership):
    """Authorization primitive; caller must first scope actor to host/session.

    Only the actual tenant owner can configure another ACTIVE admin's policy.
    It cannot be used to reset/change an owner, impersonate an administrator,
    or act across workspaces.
    """
    if actor_membership is None or target_membership is None:
        return False
    if actor_membership.tenant_id != target_membership.tenant_id:
        return False
    if actor_membership.status != MemberStatus.ACTIVE:
        return False
    if actor_membership.role != MemberRole.OWNER:
        return False
    if actor_membership.tenant.owner_id != actor_membership.user_id:
        return False
    if target_membership.status != MemberStatus.ACTIVE:
        return False
    if target_membership.role != MemberRole.ADMIN:
        return False
    return actor_membership.user_id != target_membership.user_id
