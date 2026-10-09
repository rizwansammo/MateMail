"""Store only current, verified, opt-in tenant telemetry; delete after retention."""
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.domains.models import Domain, DomainOwnership
from apps.transport_security.models import DomainTransportSecurity
from .models import TlsAggregateReport, TlsFailureBucket


@dataclass(frozen=True)
class ImportResult:
    status: str


@transaction.atomic
def store_policy(parsed):
    domain = (Domain.objects.select_related("tenant")
              .filter(domain__iexact=parsed.domain, ownership_status=DomainOwnership.VERIFIED)
              .first())
    if domain is None or domain.ownership_verified_at is None:
        return ImportResult("unmanaged")
    config = DomainTransportSecurity.objects.filter(domain=domain, enabled=True).first()
    if config is None:
        return ImportResult("unmanaged")
    # Do not reveal any report from before tenant ownership or opt-in.
    if parsed.start < max(domain.ownership_verified_at, config.created_at):
        return ImportResult("unmanaged")
    report, created = TlsAggregateReport.objects.get_or_create(
        json_sha256=parsed.digest,
        defaults=dict(
            tenant=domain.tenant, domain=domain, policy_domain=parsed.domain,
            reporter=parsed.reporter, report_id=parsed.report_id,
            policy_type=parsed.kind, period_start=parsed.start, period_end=parsed.end,
            successful_sessions=parsed.successes, failed_sessions=parsed.failures,
        ),
    )
    if not created:
        return ImportResult("duplicate")
    TlsFailureBucket.objects.bulk_create([
        TlsFailureBucket(report=report, result_type=kind, count=count)
        for kind, count in parsed.buckets if count > 0
    ])
    return ImportResult("stored")


def prune_old_reports(*, days=None):
    retention = int(days if days is not None else getattr(settings, "TLS_RPT_RETENTION_DAYS", 90))
    if not 7 <= retention <= 365:
        raise ValueError("TLS report retention must be 7–365 days")
    removed, _ = TlsAggregateReport.objects.filter(
        created_at__lt=timezone.now() - timedelta(days=retention)
    ).delete()
    return removed
