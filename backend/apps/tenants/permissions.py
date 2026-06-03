from rest_framework.permissions import BasePermission
from .models import MemberRole


class HasTenantAccess(BasePermission):
    """Request must have a resolved tenant (set by TenantMiddleware)."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.tenant)


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
    """Internal MateMail staff only."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_platform_admin
        )
