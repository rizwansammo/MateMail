from django.conf import settings


def request_host(request) -> str:
    """Return the normalized hostname for this request, without a port."""
    return request.get_host().split(":", 1)[0].rstrip(".").lower()


def dedicated_tenant_slug(request) -> str | None:
    """
    Return the tenant slug bound to this hostname, if this is a dedicated
    customer surface.

    The mapping is deployment configuration, not application data. An empty
    mapping preserves the normal multi-tenant behavior.
    """
    mapping = getattr(settings, "DEDICATED_TENANT_HOSTS", {})
    return mapping.get(request_host(request))


def scope_memberships(request, queryset):
    """Limit a TenantMembership queryset to the hostname-bound tenant."""
    slug = dedicated_tenant_slug(request)
    return queryset.filter(tenant__slug=slug) if slug else queryset


def tenant_matches_request(request, tenant) -> bool:
    """True when `tenant` is allowed on this request's hostname."""
    slug = dedicated_tenant_slug(request)
    return slug is None or (tenant is not None and tenant.slug == slug)
