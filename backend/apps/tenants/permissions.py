from rest_framework.permissions import SAFE_METHODS, BasePermission
from .models import MemberRole

ADMIN_ROLES = (MemberRole.OWNER, MemberRole.ADMIN)
SUPPORT_ROLES = (MemberRole.OWNER, MemberRole.ADMIN, MemberRole.SUPPORT)


class HasTenantAccess(BasePermission):
    """
    Request must have a resolved tenant (set by TenantMiddleware).

    NOTE: this grants access to any active member regardless of role, so it is
    only appropriate on read-only views. Any view that mutates tenant state must
    use TenantReadAdminWrite, IsTenantAdmin, or another role-aware class —
    otherwise a read_only member can mutate.
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.tenant)


class TenantReadAdminWrite(BasePermission):
    """
    Any active tenant member may read; only owner/admin may mutate.

    This is the default for tenant-owned resources whose list/detail views serve
    both reads and writes from one APIView class.
    """

    message = "Your role does not permit changes to this workspace."

    def has_permission(self, request, view):
        if not (
            request.user
            and request.user.is_authenticated
            and request.tenant
            and request.tenant_membership
        ):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.tenant_membership.role in ADMIN_ROLES


class TenantReadSupportWrite(BasePermission):
    """
    Any active tenant member may read; owner/admin/support may act.

    Explicitly narrower than TenantReadAdminWrite, for non-destructive
    diagnostic actions that support staff legitimately need — currently only
    re-running a domain's DNS check. Do not widen this without a documented
    reason: read_only members must still receive 403.
    """

    message = "Your role does not permit this action."

    def has_permission(self, request, view):
        if not (
            request.user
            and request.user.is_authenticated
            and request.tenant
            and request.tenant_membership
        ):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.tenant_membership.role in SUPPORT_ROLES


class IsEmailVerified(BasePermission):
    """
    Blocks unverified accounts from provisioning resources.

    Reads are always allowed so an unverified user can still see their workspace;
    only state-changing requests are refused. Applied to the domain and mailbox
    provisioning paths so signup automation cannot create mail resources before
    the address is proven.
    """

    message = "Verify your email address before provisioning domains or mailboxes."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "email_verified", False)
        )


class IsTenantOwner(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.tenant_membership):
            return False
        return request.tenant_membership.role == MemberRole.OWNER


class IsTenantAdmin(BasePermission):
    """Owner or Admin."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.tenant_membership):
            return False
        return request.tenant_membership.role in (MemberRole.OWNER, MemberRole.ADMIN)


class IsTenantSupport(BasePermission):
    """Owner, Admin, or Support."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.tenant_membership):
            return False
        return request.tenant_membership.role in (
            MemberRole.OWNER, MemberRole.ADMIN, MemberRole.SUPPORT
        )


class IsPlatformAdmin(BasePermission):
    """
    Internal MateMail staff only, and only with a session credential.

    An API key authenticates *as* the user who created it. Without the second
    check below, a key minted by a member of staff would be a platform-admin
    credential sitting in a customer's configuration file. APIKeyScopeMiddleware
    already refuses /api/platform/ outright; this is the same rule stated where
    the privilege is actually granted, so a future endpoint outside that prefix
    inherits it.
    """

    def has_permission(self, request, view):
        from apps.teams.models import APIKey

        if isinstance(getattr(request, "auth", None), APIKey):
            return False
        if getattr(request, "_mm_api_key", None) is not None:
            return False
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_platform_admin
        )
