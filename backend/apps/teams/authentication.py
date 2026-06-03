import hashlib

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class APIKeyAuthentication(BaseAuthentication):
    """
    Authenticates requests using Bearer mm_... API keys.
    Works alongside TenantMiddleware: the middleware resolves request.tenant
    from the key, and this class resolves request.user via DRF's auth pipeline.
    """

    def authenticate(self, request):
        # TenantMiddleware caches the resolved user to avoid a second DB lookup
        django_request = getattr(request, "_request", request)
        cached_user = getattr(django_request, "_mm_api_key_user", None)
        if cached_user is not None:
            return (cached_user, None)

        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth.startswith("Bearer mm_"):
            return None

        raw_key = auth[len("Bearer "):]
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        from apps.teams.models import APIKey
        try:
            api_key = APIKey.objects.select_related("created_by").get(
                key_hash=key_hash, is_active=True
            )
        except APIKey.DoesNotExist:
            raise AuthenticationFailed("Invalid API key.")

        if api_key.expires_at and timezone.now() > api_key.expires_at:
            raise AuthenticationFailed("API key has expired.")

        APIKey.objects.filter(id=api_key.id).update(last_used_at=timezone.now())
        return (api_key.created_by, api_key)

    def authenticate_header(self, request):
        return "Bearer"
