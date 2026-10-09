"""Read-only DMARC reporting. Tenant administrators see only their domains."""
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.models import Domain
from apps.tenants.permissions import IsTenantAdmin, IsPlatformAdmin

from .models import AggregateRecord, AggregateReport


def _days(request):
    try:
        days = int(request.query_params.get("days", "30"))
    except (TypeError, ValueError):
        return None
    return days if 1 <= days <= 90 else None


def _summarize(reports, days):
    qs = reports.filter(period_end__gte=timezone.now() - timedelta(days=days))
    totals = qs.aggregate(
        reports=Sum("message_count"),
        spf_pass=Sum("spf_pass"),
        dkim_pass=Sum("dkim_pass"),
        dmarc_pass=Sum("dmarc_pass"),
    )
    # A group-by runs over the aggregate rows, not individual messages.
    top_sources = list(
        AggregateRecord.objects.filter(report__in=qs)
        .values("source_ip")
        .annotate(messages=Sum("count"))
        .order_by("-messages", "source_ip")[:20]
    )
    sources = [{"ip": row["source_ip"], "messages": row["messages"]}
               for row in top_sources]
    recent = list(qs.order_by("-period_end").values(
        "id", "policy_domain", "reporter", "period_start", "period_end",
        "message_count", "spf_pass", "dkim_pass", "dmarc_pass",
    )[:40])
    for entry in recent:
        entry["id"] = str(entry["id"])
    return {
        "days": days,
        "report_count": qs.count(),
        "message_count": totals["reports"] or 0,
        "spf_pass": totals["spf_pass"] or 0,
        "dkim_pass": totals["dkim_pass"] or 0,
        "dmarc_pass": totals["dmarc_pass"] or 0,
        "top_sources": sources,
        "reports": recent,
        # Third-party aggregate XML is unauthenticated telemetry;
        # never treat it as an authoritative delivery or enforcement signal.
        "notice": "Aggregate reports are reporter-provided telemetry, not guaranteed inbox placement.",
    }


class DomainDmarcReportsView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request, pk):
        domain = Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()
        if domain is None:
            return Response({"detail": "Not found."}, status=404)
        days = _days(request)
        if days is None:
            return Response({"detail": "days must be 1–90."}, status=400)
        qs = AggregateReport.objects.filter(domain=domain, tenant=request.tenant)
        return Response({"domain": domain.domain, **_summarize(qs, days)})


class PlatformDmarcReportsView(APIView):
    """Platform staff sees its own sender, never a cross-tenant data dump."""
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        days = _days(request)
        if days is None:
            return Response({"detail": "days must be 1–90."}, status=400)
        from django.conf import settings
        platform_domain = getattr(
            settings, "DMARC_REPORT_PLATFORM_DOMAIN", "mail.matemail.pro"
        ).lower().rstrip(".")
        qs = AggregateReport.objects.filter(
            tenant__isnull=True, domain__isnull=True, policy_domain=platform_domain
        )
        return Response({"domain": platform_domain, **_summarize(qs, days)})
