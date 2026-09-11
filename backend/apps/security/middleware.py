"""
API key scope enforcement.

This is middleware rather than a DRF permission class on purpose. DRF's
`DEFAULT_PERMISSION_CLASSES` is *replaced*, not extended, by any view that sets
`permission_classes` — which is nearly every view in this project — so a global
default would enforce nothing. Middleware sees every request regardless of what
a view declares, and it cannot be switched off by forgetting to include a class
in a list.

It runs after TenantMiddleware, which has already resolved the key for tenant
paths. For the paths TenantMiddleware skips (platform, internal, auth) no
lookup is needed: the presence of an `mm_` bearer token on those prefixes is
itself the answer.
"""
import logging

from django.http import JsonResponse

from .scopes import FORBIDDEN_PREFIXES, permits, required_scope

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer mm_"


def _presents_api_key(request) -> bool:
    return (request.META.get("HTTP_AUTHORIZATION") or "").startswith(_BEARER_PREFIX)


class APIKeyScopeMiddleware:
    """Refuses any API-key request the key's scopes do not authorise."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info

        # Hard denial first, and without a database lookup: these prefixes are
        # not customer surface, so a customer credential never reaches them —
        # not even to be told the key is invalid.
        if path.startswith(FORBIDDEN_PREFIXES) and _presents_api_key(request):
            logger.warning("API key presented on a forbidden prefix: %s", path)
            return JsonResponse(
                {"detail": "API keys cannot be used on this endpoint."},
                status=403,
            )

        api_key = getattr(request, "_mm_api_key", None)
        if api_key is None:
            return self.get_response(request)

        if not permits(api_key.scopes, path, request.method):
            needed = required_scope(path, request.method)
            logger.info(
                "API key %s denied on %s %s (needs %s)",
                api_key.key_prefix, request.method, path, needed or "no scope",
            )
            return JsonResponse(
                {
                    "detail": (
                        f"This API key does not have the '{needed}' scope."
                        if needed
                        else "API keys cannot be used on this endpoint."
                    )
                },
                status=403,
            )

        return self.get_response(request)
