import logging

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import DisallowedHost
from django.http import HttpResponseBadRequest

from .custom_hosts import (
    CustomHostnameValueError,
    custom_hostname_cache_key,
    normalize_hostname,
)
from .models import CustomHostname, CustomHostnameProvisioningStatus

logger = logging.getLogger(__name__)


def _request_hostname(request) -> str:
    """
    Return the request hostname without a port.

    Dynamic mode sets Django ALLOWED_HOSTS to ["*"] so get_host() performs
    syntax validation but not the application allowlist. This middleware then
    performs the real allowlist check before any other middleware runs.
    """
    raw = request.get_host().strip()

    if raw.startswith("["):
        # IPv6 literals are never customer custom hostnames, but fixed/internal
        # callers can still be evaluated against the fixed list if one is ever
        # configured explicitly.
        end = raw.find("]")
        host = raw[1:end] if end > 0 else raw
    elif raw.count(":") == 1:
        host, maybe_port = raw.rsplit(":", 1)
        host = host if maybe_port.isdigit() else raw
    else:
        host = raw

    return normalize_hostname(host)


class CustomHostnameHostGuardMiddleware:
    """
    Dynamic Host allowlist for customer-owned hostnames.

    Disabled until the edge/routing phases are ready. When enabled, Django's
    static ALLOWED_HOSTS becomes ["*"] only so arbitrary customer names reach
    this guard; this class MUST therefore remain the first middleware.

    Fixed MateMail/internal hosts are accepted from CUSTOM_HOST_FIXED_HOSTS.
    A customer hostname is accepted only when its database row is ACTIVE.
    READY is intentionally refused: Phase 3 may have a certificate before
    Phase 4 makes tenant routing safe.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "CUSTOM_HOSTS_DYNAMIC_ENABLED", False):
            return self.get_response(request)

        try:
            hostname = _request_hostname(request)
        except (DisallowedHost, CustomHostnameValueError):
            return HttpResponseBadRequest("Invalid host.")

        fixed = {
            normalize_hostname(value)
            for value in getattr(settings, "CUSTOM_HOST_FIXED_HOSTS", ())
            if value
        }
        if hostname in fixed:
            return self.get_response(request)

        key = custom_hostname_cache_key(hostname)
        allowed = cache.get(key)
        if allowed is None:
            try:
                allowed = CustomHostname.objects.filter(
                    hostname=hostname,
                    provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
                ).exists()
            except Exception:
                # An unknown host must never become accepted because the
                # allowlist datastore is unavailable.
                logger.exception("Custom hostname allowlist lookup failed")
                allowed = False
            cache.set(
                key,
                bool(allowed),
                timeout=getattr(settings, "CUSTOM_HOST_CACHE_TTL", 30),
            )

        if not allowed:
            return HttpResponseBadRequest("Invalid host.")

        return self.get_response(request)
