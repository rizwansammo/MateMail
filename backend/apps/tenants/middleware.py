import hashlib

from django.http import JsonResponse
from django.utils import timezone
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .models import TenantMembership


class TenantMiddleware:
    """
    Resolves the current tenant from the authenticated user's JWT (or API key)
    and attaches it to request.tenant.

    For endpoints under /api/platform/ the middleware is skipped — platform
    admins operate cross-tenant.

    For unauthenticated requests (public pages, auth endpoints) the middleware
    sets request.tenant = None.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._jwt_auth = JWTAuthentication()

    def __call__(self, request):
        request.tenant = None
        request.tenant_membership = None

        # Platform admin and auth routes don't need tenant context
        path = request.path_info
        if path.startswith("/api/platform/") or path.startswith("/api/auth/") or path.startswith("/api/health/"):
            return self.get_response(request)

        # Attempt JWT authentication
        try:
            result = self._jwt_auth.authenticate(request)
        except (InvalidToken, TokenError):
            return self.get_response(request)

        if result is None:
            # No JWT — try API key authentication
            self._try_api_key_auth(request)
            return self.get_response(request)

        user, token = result

        # Resolve tenant from token claim or fall back to primary membership
        tenant_id = token.get("tenant_id")
        if tenant_id:
            try:
                membership = TenantMembership.objects.select_related("tenant").get(
                    tenant_id=tenant_id,
                    user=user,
                    status="active",
                )
                request.tenant = membership.tenant
                request.tenant_membership = membership
            except TenantMembership.DoesNotExist:
                return JsonResponse(
                    {"detail": "Tenant not found or access revoked."},
                    status=403,
                )
        else:
            # Fall back: use the first active membership
            membership = (
                TenantMembership.objects.select_related("tenant")
                .filter(user=user, status="active")
                .order_by("created_at")
                .first()
            )
            if membership:
                request.tenant = membership.tenant
                request.tenant_membership = membership

        return self.get_response(request)

    def _try_api_key_auth(self, request):
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth.startswith("Bearer mm_"):
            return
        raw_key = auth[len("Bearer "):]
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        try:
            from apps.teams.models import APIKey
            api_key = APIKey.objects.select_related("tenant", "created_by").get(
                key_hash=key_hash, is_active=True
            )
        except Exception:
            return
        if api_key.expires_at and timezone.now() > api_key.expires_at:
            return
        # Cache user so APIKeyAuthentication (DRF) avoids a second DB lookup
        request._mm_api_key_user = api_key.created_by
        request._mm_api_key_id = api_key.id
        request.tenant = api_key.tenant
        try:
            membership = TenantMembership.objects.select_related("tenant").get(
                tenant=api_key.tenant,
                user=api_key.created_by,
                status="active",
            )
            request.tenant_membership = membership
        except TenantMembership.DoesNotExist:
            pass
