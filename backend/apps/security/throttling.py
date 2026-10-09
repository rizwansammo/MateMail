"""
DRF throttle classes.

Two problems with the stock classes as this project had them configured:

1. `BaseThrottle.get_ident` trusts `X-Forwarded-For` wholesale when
   `NUM_PROXIES` is unset, so the per-IP identity is whatever the caller says
   it is. These classes key on `get_client_ip` instead.

2. The global throttle applied to *every* path, including `/api/internal/`.
   That prefix is the Mail Engine policy bridge: Postfix consults it per
   message. Sixty requests a minute is a mail outage, not an abuse control.
   nginx already denies the prefix at the edge and the views authenticate with
   `INTERNAL_API_SECRET`, so a request-count limit there protects nothing and
   breaks delivery. Health checks are exempt for the same reason.
"""
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from .client_ip import get_client_ip

#: Paths whose callers are our own infrastructure, not the public.
EXEMPT_PREFIXES = ("/api/health/", "/api/internal/")


class _TrustedIdentMixin:
    def allow_request(self, request, view):
        if request.path_info.startswith(EXEMPT_PREFIXES):
            return True
        return super().allow_request(request, view)

    def get_ident(self, request):
        ip = get_client_ip(request)
        if ip:
            return ip
        # No usable address: bucket every such caller together rather than
        # falling back to the spoofable header DRF would use.
        return "unknown-client"


class MateMailAnonThrottle(_TrustedIdentMixin, AnonRateThrottle):
    """Unauthenticated traffic, keyed on the real client IP."""

    scope = "anon"


class MateMailUserThrottle(_TrustedIdentMixin, UserRateThrottle):
    """Authenticated traffic, keyed on the user (IP only when anonymous)."""

    scope = "user"


class AuthEndpointThrottle(MateMailAnonThrottle):
    """
    Tight per-IP limit on the unauthenticated auth endpoints.

    Kept as defence in depth alongside the per-account and per-email limits in
    the views: this one caps the volume an IP can generate at all, before any
    request body has been trusted.
    """

    scope = "auth"


class AuthRefreshThrottle(MateMailAnonThrottle):
    """
    A separate per-IP budget for refresh-cookie exchanges.

    Session restoration may happen automatically on page load. It must never
    consume the 5/min sign-in/signup budget. This is still rate limited so a
    client cannot flood token validation with unlimited requests.
    """

    scope = "auth_refresh"


class AuthenticatedActionThrottle(UserRateThrottle):
    """
    Per-user limit for authenticated auth actions — resending verification
    email, enrolling a second factor, disabling one.

    These were previously decorated with an `AnonRateThrottle` subclass, which
    returns immediately for an authenticated request. The endpoints were
    therefore unlimited: a signed-in caller could guess TOTP codes or the
    account password without any brake at all.
    """

    scope = "auth_action"

    def get_ident(self, request):
        ip = get_client_ip(request)
        return ip or "unknown-client"
