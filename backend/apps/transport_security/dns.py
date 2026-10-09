"""Verified DNS gate for optional MTA-STS hosting. No writes or host commands."""
import re

import dns.exception
import dns.resolver
from django.conf import settings

from apps.domains.verification import check_ownership_dns

HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def policy_hostname(name: str) -> str:
    hostname = f"mta-sts.{name.lower().rstrip('.')}"
    if not HOST_RE.fullmatch(hostname) or len(hostname) > 253:
        raise ValueError("The MTA-STS hostname is invalid or too long.")
    return hostname


def validated_edge() -> str:
    edge = (getattr(settings, "MTA_STS_POLICY_EDGE_TARGET", "") or "").lower().rstrip(".")
    if not edge or not HOST_RE.fullmatch(edge):
        raise ValueError("The MTA-STS policy gateway is not configured.")
    return edge


def check_domain_policy_dns(domain) -> tuple[bool, str]:
    """Check *current* ownership, exact CNAME and ALL MX hosts, without persistence.

    A stale verified database flag alone is never enough to issue a public TLS
    certificate. Both the verifier and root worker must check independently.
    """
    if not domain.is_ownership_verified:
        return False, "Verify ownership of your email domain first."

    expected, _, _ = check_ownership_dns(domain)
    if not expected:
        return False, "Domain ownership TXT could not be confirmed. Restore it and retry."

    try:
        host, edge = policy_hostname(domain.domain), validated_edge()
    except ValueError as exc:
        return False, str(exc)
    mx = getattr(settings, "MAIL_HOSTNAME", "mx.matemail.pro").lower().rstrip(".")
    if not HOST_RE.fullmatch(mx):
        return False, "The provider mail hostname is not configured."

    try:
        cname_answers = dns.resolver.resolve(host, "CNAME", lifetime=5)
        aliases = [str(a.target).rstrip(".").lower() for a in cname_answers]
        if len(aliases) != 1 or aliases[0] != edge:
            return False, "The MTA-STS CNAME does not point to the configured policy gateway."
        mx_answers = dns.resolver.resolve(domain.domain, "MX", lifetime=5)
        hosts = [str(a.exchange).rstrip(".").lower() for a in mx_answers]
        if not hosts or any(h != mx for h in hosts):
            return False, "All MX records must match the MateMail mail server before MTA-STS setup."
    except (dns.exception.DNSException, ValueError, AttributeError):
        return False, "MTA-STS DNS records could not be verified yet. Please try again later."
    return True, "MTA-STS CNAME, MX and current domain ownership verified."
