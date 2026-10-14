"""Phase 6E Platform Mail Center reporting: bounded, privacy-minimised aggregates.

The DMARC section is limited to the platform's two reserved sender domains,
with tenant and domain *both* NULL. TLS statistics are intentionally only
global counts/sums over already bound tenant reports: no tenant, domain,
reporter, failure bucket, IP, or individual report identity is exposed.
This endpoint never touches raw reports, IMAP, native mailboxes or ingestion.
"""
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.dmarc_reports.models import AggregateReport, IngestCursor
from apps.tls_reports.models import TlsAggregateReport, TlsIngestCursor
from apps.tenants.permissions import IsPlatformAdmin

from .audit import record_platform_action

DMARC_PLATFORM_DOMAINS = ("mail.matemail.pro", "system.matemail.pro")
DMARC_RECEIVER_ADDRESSES = ("dmarc@mail.matemail.pro", "dmarc@system.matemail.pro")
TLS_RECEIVER_ADDRESSES = ("tlsrpt@mail.matemail.pro", "tlsrpt@system.matemail.pro")
MAX_DAYS = 90
RECEIVER_STALE_AFTER = timedelta(hours=2)


def _days(query_params):
    """Accept one days parameter only. Never accept a tenant/domain selector."""
    if set(query_params) - {"days"}:
        return None
    if any(len(query_params.getlist(key)) != 1 for key in query_params):
        return None
    try:
        days = int(query_params.get("days", "30"))
    except (TypeError, ValueError):
        return None
    return days if 1 <= days <= MAX_DAYS else None


def _series(qs, *, days, first_day, fields):
    """Return a fixed UTC calendar, even if data was not received on a day."""
    rows = (
        qs.annotate(day=TruncDate("period_end", tzinfo=dt_timezone.utc))
        .values("day")
        .annotate(
            report_count=Count("id"),
            **{key: Sum(model_field) for key, model_field in fields.items()},
        )
        .order_by("day")
    )
    by_day = {row["day"]: row for row in rows}
    return [
        {
            "date": (first_day + timedelta(days=offset)).isoformat(),
            "report_count": int(by_day.get(first_day + timedelta(days=offset), {}).get("report_count") or 0),
            **{
                key: int(by_day.get(first_day + timedelta(days=offset), {}).get(key) or 0)
                for key in fields
            },
        }
        for offset in range(days)
    ]


def _totals(qs, fields):
    data = qs.aggregate(
        report_count=Count("id"),
        **{key: Sum(model_field) for key, model_field in fields.items()},
    )
    return {
        "report_count": int(data["report_count"] or 0),
        **{key: int(data[key] or 0) for key in fields},
    }


def _receiver_snapshot(model, *, addresses, enabled, primary, dual, now):
    """No arbitrary configured address can become a mailbox read target."""
    active = set(addresses if dual else (primary,))
    configured = primary in addresses
    cursors = {
        row.address: row
        for row in model.objects.filter(address__in=addresses)
    }
    results = []
    for index, address in enumerate(addresses):
        cursor = cursors.get(address)
        if not enabled:
            state = "disabled"
        elif not configured:
            state = "misconfigured"
        elif address not in active:
            state = "inactive"
        elif not cursor or cursor.last_checked_at is None:
            state = "waiting"
        elif cursor.last_checked_at > now + timedelta(minutes=5):
            state = "stale"
        elif now - cursor.last_checked_at > RECEIVER_STALE_AFTER:
            state = "stale"
        else:
            state = "operational"
        results.append({
            "receiver": "legacy" if index == 0 else "system",
            "active": bool(enabled and configured and address in active),
            "status": state,
            "last_checked_at": cursor.last_checked_at if cursor else None,
            "last_success_at": cursor.last_success_at if cursor else None,
        })
    return results


@method_decorator(never_cache, name="dispatch")
class PlatformMailCenterReportOverviewView(APIView):
    """Platform-only, grouped operational metrics without raw tenant metadata."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        if not getattr(settings, "PLATFORM_MAIL_CENTER_ENABLED", False):
            return Response({"detail": "Platform Mail Center is not enabled."}, status=503)
        if not getattr(settings, "PLATFORM_MAIL_CENTER_REPORTS_ENABLED", False):
            return Response({"detail": "Platform reporting dashboard is not enabled."}, status=503)
        days = _days(request.query_params)
        if days is None:
            return Response({"detail": "days must be a single integer from 1 to 90."}, status=400)

        now = timezone.now()
        today = now.astimezone(dt_timezone.utc).date()
        first_day = today - timedelta(days=days - 1)
        start = datetime.combine(first_day, time.min, tzinfo=dt_timezone.utc)

        platform_dmarc = AggregateReport.objects.filter(
            tenant__isnull=True,
            domain__isnull=True,
            policy_domain__in=DMARC_PLATFORM_DOMAINS,
            period_end__gte=start,
            period_end__lte=now,
        )
        # TLS reports belong to tenants. Aggregate numbers only, never per
        # tenant or per domain; the response contains no row identifiers.
        tenant_tls = TlsAggregateReport.objects.filter(
            tenant__isnull=False,
            domain__isnull=False,
            period_end__gte=start,
            period_end__lte=now,
        )

        d_fields = {
            "message_count": "message_count",
            "spf_pass": "spf_pass",
            "dkim_pass": "dkim_pass",
            "dmarc_pass": "dmarc_pass",
        }
        t_fields = {
            "successful_sessions": "successful_sessions",
            "failed_sessions": "failed_sessions",
        }

        dmarc = {
            "scope": "platform_senders_only",
            **_totals(platform_dmarc, d_fields),
            "daily": _series(platform_dmarc, days=days, first_day=first_day, fields=d_fields),
        }
        tls = {
            "scope": "all_tenants_aggregate_only",
            **_totals(tenant_tls, t_fields),
            "daily": _series(tenant_tls, days=days, first_day=first_day, fields=t_fields),
        }

        receivers = {
            "dmarc": _receiver_snapshot(
                IngestCursor,
                addresses=DMARC_RECEIVER_ADDRESSES,
                enabled=bool(getattr(settings, "DMARC_REPORT_INGEST_ENABLED", False)),
                primary=getattr(settings, "DMARC_REPORT_ADDRESS", ""),
                dual=bool(getattr(settings, "DMARC_REPORT_DUAL_RECEIVER_ENABLED", False)),
                now=now,
            ),
            "tls": _receiver_snapshot(
                TlsIngestCursor,
                addresses=TLS_RECEIVER_ADDRESSES,
                enabled=bool(getattr(settings, "TLS_RPT_INGEST_ENABLED", False)),
                primary=getattr(settings, "TLS_RPT_REPORT_ADDRESS", ""),
                dual=bool(getattr(settings, "TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED", False)),
                now=now,
            ),
        }

        # The audit record carries only the reporting interval; no tenant
        # identity, individual sender, report or recipient is persisted.
        record_platform_action(
            actor=request.user, request=request,
            action="mail_center.reports_overview_read",
            target_label="platform_reporting",
            metadata={"days": days},
        )

        return Response({
            "window": {
                "days": days,
                "start": first_day.isoformat(),
                "end": today.isoformat(),
                "timezone": "UTC",
            },
            "dmarc": dmarc,
            "tls": tls,
            "receivers": receivers,
            "notice": (
                "Third-party aggregate reports are telemetry, not proof of "
                "inbox placement or complete delivery. TLS totals are "
                "platform-wide aggregates with no tenant breakdown."
            ),
        })
