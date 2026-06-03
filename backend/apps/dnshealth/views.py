from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.permissions import HasTenantAccess
from apps.domains.models import Domain
from .models import DNSRecordCheck
from .serializers import DNSRecordCheckSerializer
from .services import check_dns_for_domain


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
    """POST /api/domains/{id}/check/ — trigger synchronous DNS check, return updated domain + records."""
    permission_classes = [IsAuthenticated, HasTenantAccess]

    def post(self, request, pk):
        domain = Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()
        if not domain:
            return Response({"detail": "Not found."}, status=404)

        domain = check_dns_for_domain(domain)

        records = (
            DNSRecordCheck.objects
            .for_tenant(request.tenant)
            .filter(domain=domain)
            .order_by("record_type", "host")
        )
        from apps.domains.serializers import DomainSerializer
        return Response({
            "domain": DomainSerializer(domain).data,
            "records": DNSRecordCheckSerializer(records, many=True).data,
        })
