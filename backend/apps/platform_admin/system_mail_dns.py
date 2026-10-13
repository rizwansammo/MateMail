"""Read-only public DNS evidence for the planned system mail domain.

DNS observations never authorize provisioning, key rotation or cutover.
In particular, a published DKIM TXT record does not prove that its public
key matches a key held by Native Engine. Nothing here performs a write.
"""
from __future__ import annotations

import dns.exception
import dns.resolver

SYSTEM_DOMAIN = "system.matemail.pro"
MAIL_EXCHANGER = "mx.matemail.pro"
SELECTOR = "mm1"


def _answer(resolver, name: str, kind: str) -> list[str]:
    try:
        records = resolver.resolve(name, kind, lifetime=2.0)
        if kind == "TXT":
            return [b"".join(item.strings).decode("utf-8", "replace") for item in records]
        if kind == "MX":
            return [
                f"{item.preference} {str(item.exchange).lower().rstrip('.')}"
                for item in records
            ]
        return [str(item).strip() for item in records]
    except (dns.exception.DNSException, OSError):
        # NXDOMAIN, timeout and resolver errors all fail closed.
        return []


def inspect_system_mail_dns(*, resolver=None) -> dict:
    lookup = resolver if resolver is not None else dns.resolver.Resolver()
    mx = _answer(lookup, SYSTEM_DOMAIN, "MX")
    spf = [r for r in _answer(lookup, SYSTEM_DOMAIN, "TXT")
           if r.lower().startswith("v=spf1")]
    dmarc = [r for r in _answer(lookup, f"_dmarc.{SYSTEM_DOMAIN}", "TXT")
             if r.lower().startswith("v=dmarc1;")]
    dkim = [r for r in _answer(
        lookup, f"{SELECTOR}._domainkey.{SYSTEM_DOMAIN}", "TXT"
    ) if r.lower().startswith("v=dkim1;")]

    mx_ok = mx == [f"10 {MAIL_EXCHANGER}"]
    # A duplicate SPF record makes SPF invalid even if one copy is correct.
    spf_ok = len(spf) == 1 and "include:_spf.matemail.pro" in spf[0].lower().split()
    dmarc_ok = len(dmarc) == 1 and any(
        piece.strip().lower() in ("p=none", "p=quarantine", "p=reject")
        for piece in dmarc[0].split(";")
    )
    # Do not compare to a secret or infer signing readiness from DNS alone.
    dkim_published = len(dkim) == 1 and any(
        piece.strip().lower().startswith("p=") and len(piece.strip()) > 2
        for piece in dkim[0].split(";")
    )

    return {
        "domain": SYSTEM_DOMAIN,
        "expected_mx": f"10 {MAIL_EXCHANGER}",
        "mx": "observed" if mx_ok else "missing_or_mismatched",
        "spf": "observed" if spf_ok else "missing_or_mismatched",
        "dmarc": "observed" if dmarc_ok else "missing_or_mismatched",
        "dkim_publication": "published_key_unmatched" if dkim_published else "missing_or_mismatched",
        "dkim_engine_key_verified": False,
        "inbound_delivery_verified": False,
        "outbound_delivery_verified": False,
        "cutover_ready": False,
        "note": (
            "DNS publication is only one release gate. The actual DKIM key, "
            "mailbox credentials, delivery, reporting and rollback require "
            "separate verified evidence before any runtime switch."
        ),
    }
