import dns.resolver
import dns.exception
from django.conf import settings
from django.utils import timezone

from .models import DNSCheckStatus, DNSRecordCheck


def _mail_hostname():
    return getattr(settings, "MAIL_HOSTNAME", "mx.matemail.online")


def _mail_domain():
    return getattr(settings, "MAIL_DOMAIN", "matemail.online")


def _expected_records(domain_obj):
    d = domain_obj.domain
    sel = domain_obj.dkim_selector
    mh = _mail_hostname()
    md = _mail_domain()
    pub = domain_obj.dkim_public_key

    return [
        {
            "record_type": "MX",
            "host": d,
            "expected_value": f"10 {mh}",
            "label": "MX",
            "match_contains": None,
        },
        {
            "record_type": "TXT",
            "host": d,
            "expected_value": f"v=spf1 include:{md} ~all",
            "label": "SPF",
            "match_contains": f"include:{md}",
            "record_prefix": "v=spf1",
        },
        {
            "record_type": "TXT",
            "host": f"{sel}._domainkey.{d}",
            "expected_value": f"v=DKIM1; k=rsa; p={pub}" if pub else "v=DKIM1; k=rsa; p=<pending>",
            "label": "DKIM",
            "match_contains": "v=DKIM1",
            "record_prefix": "v=DKIM1",
        },
        {
            "record_type": "TXT",
            "host": f"_dmarc.{d}",
            "expected_value": f"v=DMARC1; p=none; rua=mailto:dmarc@{md}",
            "label": "DMARC",
            "match_contains": "v=DMARC1",
            "record_prefix": "v=DMARC1",
        },
    ]


def _resolve_mx(hostname):
    try:
        answers = dns.resolver.resolve(hostname, "MX", lifetime=5)
        return [f"{r.preference} {str(r.exchange).rstrip('.')}" for r in answers]
    except Exception:
        return []


def _resolve_txt(hostname):
    try:
        answers = dns.resolver.resolve(hostname, "TXT", lifetime=5)
        return [b"".join(r.strings).decode("utf-8", errors="replace") for r in answers]
    except Exception:
        return []


def _evaluate_mx(detected):
    mh = _mail_hostname().lower()
    for v in detected:
        parts = v.lower().split()
        if len(parts) == 2 and parts[1] == mh:
            return DNSCheckStatus.VERIFIED, v
    if not detected:
        return DNSCheckStatus.MISSING, ""
    return DNSCheckStatus.FAILED, "; ".join(detected[:3])


def _evaluate_txt(detected, match_contains, record_prefix=None):
    # A hostname can legitimately have many unrelated TXT records (Google site
    # verification, Microsoft verification, etc.). Only records belonging to
    # the protocol being checked should influence its result or appear as the
    # detected value.
    candidates = detected
    if record_prefix:
        prefix = record_prefix.lower()
        candidates = [v for v in detected if v.lstrip().lower().startswith(prefix)]

    for v in candidates:
        if match_contains and match_contains.lower() in v.lower():
            return DNSCheckStatus.VERIFIED, v
    if not candidates:
        return DNSCheckStatus.MISSING, ""
    return DNSCheckStatus.FAILED, "; ".join(candidates[:2])


def check_dns_for_domain(domain_obj):
    """
    Run all DNS checks for domain_obj. Upserts DNSRecordCheck rows,
    recalculates dns_health_score, and updates domain.status / verified_at.
    Returns the updated domain_obj.
    """
    from apps.domains.models import DomainStatus

    now = timezone.now()
    records = _expected_records(domain_obj)
    verified = {}

    for rec in records:
        rtype = rec["record_type"]
        host = rec["host"]

        if rtype == "MX":
            detected = _resolve_mx(host)
            status, detected_value = _evaluate_mx(detected)
        else:
            detected = _resolve_txt(host)
            status, detected_value = _evaluate_txt(
                detected,
                rec["match_contains"],
                rec.get("record_prefix"),
            )

        DNSRecordCheck.objects.update_or_create(
            domain=domain_obj,
            record_type=rtype,
            host=host,
            defaults={
                "tenant": domain_obj.tenant,
                "expected_value": rec["expected_value"],
                "detected_value": detected_value,
                "status": status,
                "last_checked": now,
            },
        )
        verified[rec["label"]] = status == DNSCheckStatus.VERIFIED

    domain_obj.dns_health_score = sum(1 for v in verified.values() if v) * 25

    if verified.get("MX") and verified.get("SPF"):
        domain_obj.status = DomainStatus.ACTIVE
        if not domain_obj.verified_at:
            domain_obj.verified_at = now
    elif any(verified.values()):
        domain_obj.status = DomainStatus.WARNING
    else:
        domain_obj.status = DomainStatus.PENDING

    domain_obj.save(update_fields=["dns_health_score", "status", "verified_at"])
    return domain_obj
