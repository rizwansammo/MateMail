from django.conf import settings

from .custom_hosts import active_custom_hostname_binding


def request_host(request) -> str:
    """Return the normalized hostname for this request, without a port."""
    return request.get_host().split(":", 1)[0].rstrip(".").lower()


def custom_hostname_binding(request) -> dict[str, str] | None:
    """
    Return the ACTIVE database-backed custom-host binding for this request.

    Fixed deployment bindings (DEDICATED_TENANT_HOSTS) predate the customer
    custom-domain feature and deliberately remain separate. They are the
    compatibility path for the current NetaMate deployment until Phase 5
    imports it into the normal custom-host table.
    """
    if not getattr(settings, "CUSTOM_HOSTS_DYNAMIC_ENABLED", False):
        return None
    return active_custom_hostname_binding(request_host(request))


def dedicated_tenant_slug(request) -> str | None:
    """
    Return the tenant slug bound to this hostname, if any.

    Resolution order:
      1. explicit deployment compatibility binding;
      2. ACTIVE customer custom-host binding from the database.

    Canonical MateMail hosts have neither and keep the normal shared behavior.
    """
    hostname = request_host(request)
    mapping = getattr(settings, "DEDICATED_TENANT_HOSTS", {})
    slug = mapping.get(hostname)
    if slug:
        return slug

    binding = custom_hostname_binding(request)
    return binding["tenant_slug"] if binding else None


def custom_hostname_surface(request) -> str | None:
    """Return `hub` or `postbox` for an ACTIVE customer custom hostname."""
    binding = custom_hostname_binding(request)
    return binding["surface"] if binding else None


def scope_memberships(request, queryset):
    """Limit a TenantMembership queryset to the hostname-bound tenant."""
    slug = dedicated_tenant_slug(request)
    return queryset.filter(tenant__slug=slug) if slug else queryset


def tenant_matches_request(request, tenant) -> bool:
    """True when `tenant` is allowed on this request's hostname."""
    slug = dedicated_tenant_slug(request)
    return slug is None or (tenant is not None and tenant.slug == slug)
