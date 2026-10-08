"""
Customer-owned Hub/PostBox hostname validation and DNS verification.

This module deliberately has no nginx or Certbot code. The application owns the
logical hostname -> tenant -> surface mapping; Phase 3's root-owned host worker
owns edge configuration and certificates.
"""
from __future__ import annotations

import ipaddress
import logging
import re

import dns.exception
import dns.resolver
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import (
    CustomHostname,
    CustomHostnameDNSStatus,
    CustomHostnameProvisioningStatus,
)

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DNS_TIMEOUT = 5.0


class CustomHostnameValueError(ValueError):
    """A customer-safe validation error."""


def normalize_hostname(value: str) -> str:
    """
    Return a lower-case ASCII hostname with no trailing dot.

    This performs syntax normalization only. Customer policy (no IP literals,
    no MateMail-owned names, at least two labels) lives in
    validate_customer_hostname so fixed/internal hosts can reuse this helper.
    """
    value = (value or "").strip().rstrip(".").lower()
    if not value:
        raise CustomHostnameValueError("Enter a hostname.")
    if "://" in value or "/" in value or "\\" in value:
        raise CustomHostnameValueError("Enter a hostname, not a URL.")
    if ":" in value:
        raise CustomHostnameValueError("Do not include a port number.")
    if "*" in value or "_" in value:
        raise CustomHostnameValueError("Wildcards and underscores are not supported.")

    try:
        ascii_value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise CustomHostnameValueError("Enter a valid hostname.") from exc

    if len(ascii_value) > 253:
        raise CustomHostnameValueError("The hostname is too long.")

    labels = ascii_value.split(".")
    if any(not label or len(label) > 63 or not _LABEL_RE.fullmatch(label) for label in labels):
        raise CustomHostnameValueError("Enter a valid hostname.")

    return ascii_value


def validate_customer_hostname(value: str) -> str:
    """Normalize and apply the policy for a customer-owned HTTPS hostname."""
    hostname = normalize_hostname(value)

    if "." not in hostname:
        raise CustomHostnameValueError("Use a fully qualified hostname, for example mail.company.com.")

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise CustomHostnameValueError("Use a hostname, not an IP address.")

    final_label = hostname.rsplit(".", 1)[-1]
    if len(final_label) < 2 or final_label.isdigit():
        raise CustomHostnameValueError("Use a public hostname with a valid domain suffix.")

    reserved_suffixes = getattr(
        settings,
        "CUSTOM_HOST_RESERVED_SUFFIXES",
        ("matemail.online", "matemail.pro"),
    )
    for suffix in reserved_suffixes:
        suffix = normalize_hostname(suffix)
        if hostname == suffix or hostname.endswith(f".{suffix}"):
            raise CustomHostnameValueError(
                "MateMail-owned hostnames cannot be registered as customer custom domains."
            )

    fixed = {
        normalize_hostname(item)
        for item in getattr(settings, "CUSTOM_HOST_FIXED_HOSTS", ())
        if item
    }
    if hostname in fixed:
        raise CustomHostnameValueError("This hostname is reserved by MateMail.")

    return hostname


def cname_target() -> str:
    """The one target every customer CNAME must point at."""
    return normalize_hostname(
        getattr(settings, "CUSTOM_HOST_CNAME_TARGET", "custom.matemail.online")
    )


def _lookup_cname_targets(hostname: str) -> tuple[list[str], str]:
    """
    Resolve the direct CNAME target.

    Returns (targets, technical_error). The technical detail is for logs only;
    callers persist a customer-safe message instead.
    """
    try:
        answers = dns.resolver.resolve(hostname, "CNAME", lifetime=_DNS_TIMEOUT)
    except dns.resolver.NXDOMAIN:
        return [], "NXDOMAIN"
    except dns.resolver.NoAnswer:
        return [], "no CNAME answer"
    except dns.resolver.NoNameservers as exc:
        return [], f"no nameservers: {exc}"
    except dns.exception.Timeout:
        return [], "timeout"
    except dns.exception.DNSException as exc:
        return [], f"{type(exc).__name__}: {exc}"

    targets = []
    for rdata in answers:
        target = getattr(rdata, "target", None)
        if target is None:
            continue
        try:
            targets.append(normalize_hostname(str(target)))
        except CustomHostnameValueError:
            continue
    return targets, ""


def check_custom_hostname_dns(hostname: str) -> tuple[bool, str, str]:
    """
    Check that the hostname directly CNAMEs to the MateMail custom-domain edge.

    A direct CNAME keeps the customer setup deterministic and makes the value
    shown in Hub exactly the value verified here.
    """
    expected = cname_target()
    targets, technical = _lookup_cname_targets(hostname)

    if technical:
        if technical in {"NXDOMAIN", "no CNAME answer"}:
            return (
                False,
                f"No CNAME record pointing to {expected} was found yet. Add the record and try again after DNS has propagated.",
                technical,
            )
        return (
            False,
            "MateMail could not read DNS for this hostname just now. Please try again in a few minutes.",
            technical,
        )

    if expected in targets:
        return True, "Custom hostname verified.", ""

    return (
        False,
        f"The CNAME does not point to {expected}. Update the DNS record and try again.",
        f"targets={targets!r}",
    )


def verify_custom_hostname_dns(custom_hostname: CustomHostname) -> tuple[bool, str]:
    """Resolve DNS and persist the customer-safe verification result."""
    found, message, technical = check_custom_hostname_dns(custom_hostname.hostname)
    now = timezone.now()

    if found:
        CustomHostname.objects.filter(pk=custom_hostname.pk).update(
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            dns_verified_at=now,
            dns_last_checked_at=now,
            last_error="",
        )
        custom_hostname.dns_status = CustomHostnameDNSStatus.VERIFIED
        custom_hostname.dns_verified_at = now
        custom_hostname.dns_last_checked_at = now
        custom_hostname.last_error = ""
        logger.info(
            "Custom hostname %s verified for tenant %s",
            custom_hostname.hostname,
            custom_hostname.tenant_id,
        )
        return True, message

    CustomHostname.objects.filter(pk=custom_hostname.pk).update(
        dns_status=CustomHostnameDNSStatus.FAILED,
        dns_last_checked_at=now,
        last_error=message,
    )
    custom_hostname.dns_status = CustomHostnameDNSStatus.FAILED
    custom_hostname.dns_last_checked_at = now
    custom_hostname.last_error = message
    logger.info(
        "Custom hostname DNS check failed for %s (tenant %s): %s",
        custom_hostname.hostname,
        custom_hostname.tenant_id,
        technical or "no matching CNAME",
    )
    return False, message


def custom_hostname_cache_key(hostname: str) -> str:
    # v2 because Phase 2 cached a boolean under the old key. Redis survives
    # application deploys, so reusing that key for the richer binding payload
    # would make a freshly deployed process read True as though it were a dict.
    return f"custom-host-binding-v2:{hostname}"


def active_custom_hostname_binding(hostname: str) -> dict[str, str] | None:
    """
    Return the ACTIVE custom-host binding for `hostname`, or None.

    This is the single application-level lookup used by both the dynamic Host
    allowlist and tenant/surface resolution. Keeping those two decisions on the
    same cached payload prevents a hostname from being accepted by one layer
    while a second layer resolves it differently.
    """
    try:
        hostname = normalize_hostname(hostname)
    except CustomHostnameValueError:
        return None

    key = custom_hostname_cache_key(hostname)
    cached = cache.get(key)
    if cached is not None:
        return cached if isinstance(cached, dict) else None

    row = (
        CustomHostname.objects.filter(
            hostname=hostname,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
        )
        .values(
            "id",
            "tenant_id",
            "tenant__slug",
            "surface",
        )
        .first()
    )
    if row is None:
        cache.set(
            key,
            False,
            timeout=getattr(settings, "CUSTOM_HOST_CACHE_TTL", 30),
        )
        return None

    binding = {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "tenant_slug": str(row["tenant__slug"]),
        "surface": str(row["surface"]),
        "hostname": hostname,
    }
    cache.set(
        key,
        binding,
        timeout=getattr(settings, "CUSTOM_HOST_CACHE_TTL", 30),
    )
    return binding


def invalidate_custom_hostname_cache(hostname: str) -> None:
    try:
        hostname = normalize_hostname(hostname)
    except CustomHostnameValueError:
        return

    # Delete the Phase 2 boolean key as well. It may still be present in Redis
    # across the first Phase 4 deployment and must never influence a request.
    cache.delete_many(
        [
            custom_hostname_cache_key(hostname),
            f"custom-host-active:{hostname}",
        ]
    )
