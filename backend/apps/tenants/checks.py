from django.conf import settings
from django.core.checks import Error, Tags, register

_GUARD = "apps.tenants.host_middleware.CustomHostnameHostGuardMiddleware"
_SURFACE_GUARD = "apps.tenants.surface_middleware.CustomHostnameSurfaceGuardMiddleware"


@register(Tags.security)
def custom_hostname_host_guard_check(app_configs, **kwargs):
    """
    Refuse an unsafe dynamic-host deployment.

    Dynamic customer names require Django's static allowlist to be open, so the
    application guard becomes the security boundary. A missing/reordered guard
    with ALLOWED_HOSTS=["*"] would be a host-header vulnerability rather than a
    feature misconfiguration.
    """
    if not getattr(settings, "CUSTOM_HOSTS_DYNAMIC_ENABLED", False):
        return []

    errors = []
    middleware = list(getattr(settings, "MIDDLEWARE", ()))
    if not middleware or middleware[0] != _GUARD:
        errors.append(
            Error(
                "Dynamic custom hosts require CustomHostnameHostGuardMiddleware first.",
                hint=f"Put {_GUARD} at MIDDLEWARE[0].",
                id="tenants.E020",
            )
        )

    if len(middleware) < 2 or middleware[1] != _SURFACE_GUARD:
        errors.append(
            Error(
                "Dynamic custom hosts require CustomHostnameSurfaceGuardMiddleware second.",
                hint=f"Put {_SURFACE_GUARD} at MIDDLEWARE[1].",
                id="tenants.E023",
            )
        )

    if list(getattr(settings, "ALLOWED_HOSTS", ())) != ["*"]:
        errors.append(
            Error(
                "Dynamic custom hosts require Django ALLOWED_HOSTS=['*'].",
                hint=(
                    "Do not enumerate customer names in ALLOWED_HOSTS; the first "
                    "middleware enforces the database-backed allowlist."
                ),
                id="tenants.E021",
            )
        )

    if not getattr(settings, "CUSTOM_HOST_FIXED_HOSTS", ()):
        errors.append(
            Error(
                "CUSTOM_HOST_FIXED_HOSTS must not be empty in dynamic-host mode.",
                id="tenants.E022",
            )
        )

    return errors
