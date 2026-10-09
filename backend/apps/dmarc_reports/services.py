"""Idempotent, verified-domain-only DMARC report storage and retention."""
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from apps.domains.models import Domain, DomainOwnership

from .models import AggregateRecord, AggregateReport
from .parser import ParsedReport


@dataclass(frozen=True)
class ImportResult:
    status: str
    policy_domain: str


@transaction.atomic
def store_report(parsed: ParsedReport) -> ImportResult:
    """No raw XML or message bodies enter PostgreSQL.

    The reported policy domain comes from a third-party XML document, not
    from the authenticated account. Bind it ONLY to a currently verified
    MateMail domain. Unrecognized/unverified domains are silently skipped.
    The platform's own isolated sender subdomain is separately operator-only.
    """
    name = parsed.policy_domain
    platform_sender = getattr(
        settings, "DMARC_REPORT_PLATFORM_DOMAIN", "mail.matemail.pro"
    ).lower().rstrip(".")
    tenant = domain = None
    if name != platform_sender:
        domain = (
            Domain.objects.select_related("tenant")
            .filter(domain__iexact=name, ownership_status=DomainOwnership.VERIFIED)
            .first()
        )
        if domain is None:
            return ImportResult("unmanaged", name)
        # A verified claim gives access to CURRENT reports, never to another
        # owner's historical sending telemetry from before the claim.
        if (domain.ownership_verified_at is None
                or parsed.period_end < domain.ownership_verified_at):
            return ImportResult("unmanaged", name)
        tenant = domain.tenant

    obj, created = AggregateReport.objects.get_or_create(
        xml_sha256=parsed.digest,
        defaults={
            "tenant": tenant,
            "domain": domain,
            "policy_domain": name,
            "reporter": parsed.reporter,
            "report_identifier": parsed.report_identifier,
            "period_start": parsed.period_start,
            "period_end": parsed.period_end,
            "message_count": parsed.count,
            "spf_pass": parsed.spf_pass,
            "dkim_pass": parsed.dkim_pass,
            "dmarc_pass": parsed.dmarc_pass,
        },
    )
    if not created:
        return ImportResult("duplicate", name)
    AggregateRecord.objects.bulk_create([
        AggregateRecord(
            report=obj, source_ip=row.source_ip, count=row.count,
            disposition=row.disposition, spf_result=row.spf_result,
            dkim_result=row.dkim_result,
        )
        for row in parsed.rows
    ])
    return ImportResult("stored", name)


def prune_old_reports(*, days: int | None = None) -> int:
    """Bound retention of source IP telemetry; does NOT touch original mail."""
    retention = days if days is not None else int(
        getattr(settings, "DMARC_REPORT_RETENTION_DAYS", 90)
    )
    if not 7 <= retention <= 365:
        raise ValueError("DMARC retention must be 7–365 days")
    deleted, _ = AggregateReport.objects.filter(
        created_at__lt=timezone.now() - timedelta(days=retention)
    ).delete()
    return deleted
