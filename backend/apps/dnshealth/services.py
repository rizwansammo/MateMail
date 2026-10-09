import dns.resolver
import dns.exception
from django.conf import settings
from django.utils import timezone

from .models import DNSCheckStatus, DNSRecordCheck


def _mail_hostname():
    return getattr(settings, "MAIL_HOSTNAME", "mx.matemail.pro")


def _legacy_mail_hostname():
    return getattr(settings, "LEGACY_MAIL_HOSTNAME", "mx.matemail.online")


def _legacy_spf_include():
    return getattr(settings, "LEGACY_SPF_INCLUDE_DOMAIN", "_spf.matemail.online")


def _legacy_autodiscover_host():
    return getattr(settings, "LEGACY_AUTODISCOVER_HOST", "autodiscover.matemail.online")


def _dmarc_report_address():
    return getattr(settings, "DMARC_REPORT_ADDRESS", "dmarc@mail.matemail.pro")


def _spf_include():
    """
    The host a customer's SPF record includes.

    Separate from `_mail_domain()` on purpose. These were the same value
    until DEC-056, which made customer SPF say `include:matemail.online` —
    the product's website domain doing double duty as the provider's SPF
    authorisation record. `_spf.matemail.online` exists only to list sending
    IPs, so it can change when the sending estate changes without touching a
    domain that serves a website.
    """
    return getattr(settings, "SPF_INCLUDE_DOMAIN", "_spf.matemail.pro")


def _autodiscover_host():
    return getattr(settings, "AUTODISCOVER_HOST", "autodiscover.matemail.pro")


def _expected_records(domain_obj):
    d = domain_obj.domain
    sel = domain_obj.dkim_selector
    mh = _mail_hostname()
    dmarc_value = "v=DMARC1; p=none"
    if getattr(settings, "DMARC_AGGREGATE_REPORTING_ENABLED", False):
        # Optional platform-wide feature: external reporting authorization is
        # set up once for the receiver domain, never once per tenant domain.
        dmarc_value += f"; rua=mailto:{_dmarc_report_address()}"
    spf = _spf_include()
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
            "expected_value": f"v=spf1 include:{spf} ~all",
            "label": "SPF",
            "match_contains": f"include:{spf}",
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
            "expected_value": dmarc_value,
            "label": "DMARC",
            "match_contains": "v=DMARC1",
            "record_prefix": "v=DMARC1",
        },
    ]


def _discovery_records(domain_obj):
    """
    Records that improve mail-client setup but are not mail configuration.

    Kept apart from `_expected_records` because the health score is
    `verified * 25` over exactly the four records that decide whether mail
    works: MX, SPF, DKIM, DMARC. Autodiscover decides whether Outlook fills
    in its own port numbers. A domain without it sends and receives mail
    perfectly, so counting it would report a healthy domain as 80% and send
    somebody looking for a fault that does not exist.

    These rows are still checked and still stored, so the UI can say whether
    the record is published. `is_scored=False` is what keeps them out of the
    arithmetic, and it is a stored field rather than an implicit consequence
    of which function built the row — so the invariant can be asserted.

    There is no `record_prefix` here: that filter exists to separate one TXT
    string from the unrelated TXT strings sharing a hostname, and an SRV
    answer is neither.
    """
    return [
        {
            "record_type": "SRV",
            "host": f"_autodiscover._tcp.{domain_obj.domain}",
            "expected_value": f"0 0 443 {_autodiscover_host()}.",
            "label": "AUTODISCOVER",
            "match_contains": _autodiscover_host(),
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
    accepted = {mh, _legacy_mail_hostname().lower()}
    for v in detected:
        parts = v.lower().split()
        if len(parts) == 2 and parts[1].rstrip(".") in accepted:
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


def _resolve_srv(hostname):
    """
    SRV targets as `priority weight port target.`, or [] if there are none.

    A domain with no SRV record is the normal case — the record is optional
    — so an NXDOMAIN here is not an error and must not be logged as one.
    """
    try:
        answers = dns.resolver.resolve(hostname, "SRV", lifetime=5)
        return [
            f"{r.priority} {r.weight} {r.port} {str(r.target).rstrip(chr(46))}."
            for r in answers
        ]
    except Exception:
        return []


def _evaluate_srv(detected, expect_target):
    """
    Verified when some SRV answer points at our Autodiscover host on 443.

    The target is compared without its trailing dot and case-insensitively,
    because a resolver returns it absolute and a zone file may not. The port
    is checked too: a record aimed at the right host on the wrong port sends
    Outlook somewhere nothing is listening.
    """
    wanted = expect_target.rstrip(chr(46)).lower()
    for value in detected:
        parts = value.split()
        if len(parts) == 4 and parts[2] == "443" and parts[3].rstrip(chr(46)).lower() == wanted:
            return DNSCheckStatus.VERIFIED, value
    if not detected:
        return DNSCheckStatus.MISSING, ""
    return DNSCheckStatus.FAILED, "; ".join(detected[:2])


def check_dns_for_domain(domain_obj):
    """
    Run all DNS checks for domain_obj. Upserts DNSRecordCheck rows,
    recalculates dns_health_score, and updates domain.status / verified_at.
    Returns the updated domain_obj.
    """
    from apps.domains.models import DomainStatus

    now = timezone.now()
    scored = _expected_records(domain_obj)
    discovery = _discovery_records(domain_obj)
    verified = {}

    for rec in scored + discovery:
        rtype = rec["record_type"]
        host = rec["host"]
        is_scored = rec in scored

        if rtype == "MX":
            detected = _resolve_mx(host)
            status, detected_value = _evaluate_mx(detected)
        elif rtype == "SRV":
            detected = _resolve_srv(host)
            status, detected_value = _evaluate_srv(detected, rec["match_contains"])
            if status != DNSCheckStatus.VERIFIED:
                legacy_status, legacy_value = _evaluate_srv(detected, _legacy_autodiscover_host())
                if legacy_status == DNSCheckStatus.VERIFIED:
                    status, detected_value = legacy_status, legacy_value
        else:
            detected = _resolve_txt(host)
            if rec["label"] == "SPF":
                # RFC 7208: two SPF policies at one hostname are invalid;
                # never mark this valid just because one record includes us.
                spf_records = [v for v in detected if v.lstrip().lower().startswith("v=spf1")]
                if len(spf_records) > 1:
                    status, detected_value = DNSCheckStatus.FAILED, "; ".join(spf_records[:2])
                else:
                    status, detected_value = _evaluate_txt(detected, rec["match_contains"], "v=spf1")
                    if status != DNSCheckStatus.VERIFIED:
                        legacy_token = "include:" + _legacy_spf_include()
                        status_legacy, value_legacy = _evaluate_txt(detected, legacy_token, "v=spf1")
                        if status_legacy == DNSCheckStatus.VERIFIED:
                            status, detected_value = status_legacy, value_legacy
            else:
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
                "is_scored": is_scored,
            },
        )
        # Only scored records reach `verified`, so the arithmetic below
        # cannot see a discovery record even if one were added carelessly.
        if is_scored:
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
