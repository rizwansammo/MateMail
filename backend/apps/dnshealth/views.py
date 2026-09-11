from rest_framework.exceptions import Throttled
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import logging

from apps.tenants.permissions import HasTenantAccess, TenantReadSupportWrite
from apps.domains.models import Domain
from apps.security import ratelimit
from apps.security.limits import DOMAIN_CHECK_PER_DOMAIN
from .models import DNSRecordCheck
from .serializers import DNSRecordCheckSerializer

logger = logging.getLogger(__name__)
from .tasks import check_domain_dns, claim_check_slot, release_check_slot


class DomainDNSRecordsView(APIView):
    """GET /api/domains/{id}/records/ — list DNS record check results."""
    permission_classes = [IsAuthenticated, HasTenantAccess]

    def get(self, request, pk):
        domain = Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()
        if not domain:
            return Response({"detail": "Not found."}, status=404)
        records = (
            DNSRecordCheck.objects
            .for_tenant(request.tenant)
            .filter(domain=domain)
            .order_by("record_type", "host")
        )
        return Response(DNSRecordCheckSerializer(records, many=True).data)


class DomainCheckDNSView(APIView):
    """
    POST /api/domains/{id}/check/ — queue a DNS health check.

    Asynchronous by design. This previously ran up to four resolver lookups
    with a 5s timeout inline, so one unresponsive nameserver could hold a
    request worker for ~20 seconds; sixteen concurrent calls took the API down.

    Returns 202 with the last known state. The client polls the domain detail
    endpoint (or re-reads `records`) to see the result — there is deliberately
    no fabricated success here.
    """

    permission_classes = [IsAuthenticated, TenantReadSupportWrite]

    def post(self, request, pk):
        domain = Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()
        if not domain:
            return Response({"detail": "Not found."}, status=404)

        # The in-flight slot below collapses a burst of clicks; this caps the
        # sustained rate, since a patient caller can wait out each slot.
        decision = ratelimit.hit(
            DOMAIN_CHECK_PER_DOMAIN.bucket,
            str(domain.id),
            limit=DOMAIN_CHECK_PER_DOMAIN.limit,
            window=DOMAIN_CHECK_PER_DOMAIN.window,
        )
        if not decision.allowed:
            raise Throttled(
                wait=decision.retry_after,
                detail=(
                    "This domain has been checked too many times in the last "
                    "hour. Please wait before checking again."
                ),
            )

        # Collapse check storms: repeated clicks, or a manual check racing the
        # periodic sweep, must not queue duplicate work for the same domain.
        queued = claim_check_slot(domain.id)
        if queued:
            try:
                check_domain_dns.delay(str(domain.id))
            except Exception:
                release_check_slot(domain.id)
                logger.error("Could not queue DNS check for %s", domain.domain)
                return Response(
                    {"detail": "Could not start the DNS check just now. Please try again shortly."},
                    status=503,
                )

        records = (
            DNSRecordCheck.objects
            .for_tenant(request.tenant)
            .filter(domain=domain)
            .order_by("record_type", "host")
        )
        from apps.domains.serializers import DomainSerializer

        return Response(
            {
                "status": "queued" if queued else "already_running",
                "detail": (
                    "DNS check started. Results usually appear within a minute."
                    if queued
                    else "A DNS check for this domain is already running."
                ),
                # Last known state, explicitly not the result of this run.
                "domain": DomainSerializer(domain).data,
                "records": DNSRecordCheckSerializer(records, many=True).data,
            },
            status=202,
        )
