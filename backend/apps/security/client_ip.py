"""
Trusted client IP resolution.

Every abuse control that keys on "the caller's IP" is only as good as this
function. `X-Forwarded-For` is set by whoever is at the other end of the socket
*and* by anyone upstream of them, so the header as a whole is attacker
controlled: an attacker who varies it per request defeats any per-IP limit that
trusts it blindly. DRF's own `BaseThrottle.get_ident` does exactly that when
`NUM_PROXIES` is unset — it joins the whole header and uses it as the identity.

MateMail sits behind host-native nginx, which sets

    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

`$proxy_add_x_forwarded_for` is "whatever the client sent, plus the address
nginx actually saw". So the *rightmost* entry is the only one our own
infrastructure wrote; everything to its left is the client's claim. With N
trusted proxy hops in front of the application, the real client is the entry N
places from the right.

Anything that does not parse as an IP address is discarded rather than used as
a cache key.
"""
import ipaddress

from django.conf import settings


def _as_ip(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    # A proxy may write "203.0.113.4:51234" or "[2001:db8::1]:443".
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1:candidate.index("]")]
    elif candidate.count(":") == 1:
        # One colon means IPv4 with a port. A bare IPv6 address has several, so
        # this cannot truncate one.
        candidate = candidate.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def trusted_proxy_count() -> int:
    return max(0, int(getattr(settings, "TRUSTED_PROXY_COUNT", 0) or 0))


def get_client_ip(request) -> str | None:
    """
    The caller's IP address, or None when it cannot be established.

    Returning None is deliberate: callers key rate limits on the result, and a
    shared "unknown" bucket is safer than a bucket an attacker can choose.
    """
    remote_addr = _as_ip(request.META.get("REMOTE_ADDR"))

    hops = trusted_proxy_count()
    if hops == 0:
        # No reverse proxy in front of us: the socket peer is the client, and
        # any X-Forwarded-For present was written by the client itself.
        return remote_addr

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR") or ""
    entries = [part for part in (p.strip() for p in forwarded.split(",")) if part]
    if len(entries) < hops:
        # Fewer hops than configured — the request did not arrive through the
        # expected chain. Trust only the socket peer.
        return remote_addr

    return _as_ip(entries[-hops]) or remote_addr
