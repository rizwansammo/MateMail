"""Tenant-admin report summaries; platform admin receives operational health only."""
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.models import Domain
from apps.tenants.permissions import IsTenantAdmin, IsPlatformAdmin
from .models import TlsAggregateReport, TlsFailureBucket, TlsIngestCursor


def _days(request):
    try:
        days = int(request.query_params.get("days", "30"))
    except (TypeError, ValueError):
        return None
    return days if 1 <= days <= 90 else None


class DomainTlsReportsView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request, pk):
        domain = Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()
        if domain is None:
            return Response({"detail": "Not found."}, status=404)
        days = _days(request)
        if days is None:
            return Response({"detail": "days must be 1–90."}, status=400)
        reports = TlsAggregateReport.objects.filter(
            domain=domain, tenant=request.tenant,
            period_end__gte=timezone.now() - timedelta(days=days),
        )
        totals = reports.aggregate(ok=Sum("successful_sessions"), failed=Sum("failed_sessions"))
        buckets = list(TlsFailureBucket.objects.filter(report__in=reports)
                       .values("result_type").annotate(count=Sum("count"))
                       .order_by("-count", "result_type")[:20])
        recent = list(reports.order_by("-period_end").values(
            "id", "reporter", "policy_type", "period_start", "period_end",
            "successful_sessions", "failed_sessions",
        )[:40])
        for row in recent:
            row["id"] = str(row["id"])
        return Response({
            "domain": domain.domain, "days": days,
            "report_count": reports.count(),
            "successful_sessions": totals["ok"] or 0,
            "failed_sessions": totals["failed"] or 0,
            "failure_types": buckets, "reports": recent,
            "notice": "TLS reports are unauthenticated third-party telemetry, not guaranteed delivery.",
        })


class PlatformTlsReceiverHealthView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        from django.conf import settings
        enabled = bool(getattr(settings, "TLS_RPT_INGEST_ENABLED", False))
        address = getattr(settings, "TLS_RPT_REPORT_ADDRESS", "tlsrpt@mail.matemail.pro")
        cursor = TlsIngestCursor.objects.filter(address=address).first()
        now = timezone.now()
        checked = cursor.last_checked_at if cursor else None
        return Response({
            "enabled": enabled,
            "status": "disabled" if not enabled else (
                "stale" if checked is None or now - checked > timedelta(hours=2)
                else "operational"),
            "last_checked_at": checked,
            "last_success_at": cursor.last_success_at if cursor else None,
            "processed_count": cursor.processed_count if cursor else 0,
            "rejected_count": cursor.rejected_count if cursor else 0,
            "reports_last_24h": TlsAggregateReport.objects.filter(
                created_at__gte=now - timedelta(hours=24)).count(),
        })
