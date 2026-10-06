import hashlib

from django.http import JsonResponse
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .models import TenantMembership
from .host_binding import bound_tenant_slug


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

        # Platform admin is never exposed through a dedicated customer host.
        # Keep this application-side as well as in nginx so a future proxy
        # misconfiguration cannot turn a customer hostname into a Platform route.
        path = request.path_info
        if path.startswith("/api/platform/"):
            if bound_tenant_slug(request):
                return JsonResponse({"detail": "Not found."}, status=404)
            return self.get_response(request)

        # Auth and health routes do not need tenant context.
        if path.startswith("/api/auth/") or path.startswith("/api/health/"):
            return self.get_response(request)

        # An API key is not a JWT, and simplejwt cannot be asked politely.
        # It claims every "Bearer ..." header and *raises* InvalidToken when
        # the value will not parse, so an `mm_` key used to take the `except`
        # branch below and return with no tenant resolved — which made every
        # role-aware permission class refuse the request. Dispatch on the
        # credential type rather than discovering it from an exception.
        if request.META.get("HTTP_AUTHORIZATION", "").startswith("Bearer mm_"):
            self._try_api_key_auth(request)
            bound_slug = bound_tenant_slug(request)
            if bound_slug and request.tenant and request.tenant.slug != bound_slug:
                return JsonResponse(
                    {"detail": "This tenant is not available on this host."},
                    status=403,
                )
            return self.get_response(request)

        # Attempt JWT authentication.
        #
        # This middleware runs outside DRF's exception handling, so any exception
        # escaping here becomes an unhandled 500 rather than a 401. simplejwt
        # raises AuthenticationFailed (not InvalidToken) for a structurally valid
        # token belonging to an inactive or deleted user, so that case must be
        # caught too. We resolve no tenant and let DRF's authentication classes
        # produce the proper 401 on the view.
        try:
            result = self._jwt_auth.authenticate(request)
        except (InvalidToken, TokenError, AuthenticationFailed):
            return self.get_response(request)

        if result is None:
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

        bound_slug = bound_tenant_slug(request)
        if bound_slug and request.tenant and request.tenant.slug != bound_slug:
            return JsonResponse({"detail": "This tenant is not available on this host."}, status=403)

        return self.get_response(request)

    def _try_api_key_auth(self, request):
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth.startswith("Bearer mm_"):
            return
        raw_key = auth[len("Bearer "):]
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        from apps.teams.models import APIKey
        try:
            api_key = APIKey.objects.select_related("tenant", "created_by").get(
                key_hash=key_hash, is_active=True
            )
        except APIKey.DoesNotExist:
            # Unknown or revoked key — resolve no tenant and let DRF's
            # APIKeyAuthentication return the 401. Any other exception is a real
            # fault and must propagate rather than silently granting no tenant.
            return
        if api_key.expires_at and timezone.now() > api_key.expires_at:
            return
        # Cache user so APIKeyAuthentication (DRF) avoids a second DB lookup.
        # The key object is attached as well: APIKeyScopeMiddleware runs before
        # DRF and needs the scopes, and DRF needs it for request.auth.
        request._mm_api_key_user = api_key.created_by
        request._mm_api_key_id = api_key.id
        request._mm_api_key = api_key
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
