from django.conf import settings

from .custom_hosts import active_custom_hostname_binding


def request_host(request) -> str:
    """Return the normalized hostname for this request, without a port."""
    return request.get_host().split(":", 1)[0].rstrip(".").lower()


def custom_hostname_binding(request) -> dict[str, str] | None:
    """Return the ACTIVE database-backed custom-host binding for this request."""
    if not getattr(settings, "CUSTOM_HOSTS_DYNAMIC_ENABLED", False):
        return None
    if hasattr(request, "custom_hostname_binding"):
        return request.custom_hostname_binding
    return active_custom_hostname_binding(request_host(request))


def bound_tenant_slug(request) -> str | None:
    """Return the tenant slug bound to this ACTIVE custom hostname, if any."""
    binding = custom_hostname_binding(request)
    return binding["tenant_slug"] if binding else None


def custom_hostname_surface(request) -> str | None:
    """Return `hub` or `postbox` for an ACTIVE customer custom hostname."""
    binding = custom_hostname_binding(request)
    return binding["surface"] if binding else None


def scope_memberships(request, queryset):
    """Limit a TenantMembership queryset to the custom-host-bound tenant."""
    slug = bound_tenant_slug(request)
    return queryset.filter(tenant__slug=slug) if slug else queryset


def tenant_matches_request(request, tenant) -> bool:
    """True when `tenant` is allowed on this request's hostname."""
    slug = bound_tenant_slug(request)
    return slug is None or (tenant is not None and tenant.slug == slug)
