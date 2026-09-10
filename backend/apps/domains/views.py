import logging

from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.utils import check_domain_limit
from apps.logs.models import LogEventType
from apps.logs.utils import log_event
from apps.tenants.permissions import IsEmailVerified, TenantReadAdminWrite
from .dkim import generate_dkim_keypair
from .models import Domain
from .serializers import DomainCreateSerializer, DomainSerializer

logger = logging.getLogger(__name__)


class DomainListCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def get(self, request):
        domains = Domain.objects.for_tenant(request.tenant).order_by("-added_at")
        return Response(DomainSerializer(domains, many=True).data)

    def post(self, request):
        allowed, msg = check_domain_limit(request.tenant)
        if not allowed:
            return Response({"detail": msg}, status=402)

        serializer = DomainCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        selector = getattr(settings, "DKIM_SELECTOR", "mm1")
        private_pem, public_key = generate_dkim_keypair()

        domain = Domain.objects.create(
            tenant=request.tenant,
            domain=serializer.validated_data["domain"],
            dkim_selector=selector,
            dkim_public_key=public_key,
            dkim_private_key=private_pem.decode(),
        )

        # Queue DNS check + mail engine provisioning asynchronously
        try:
            from apps.dnshealth.tasks import check_domain_dns
            check_domain_dns.delay(str(domain.id))
        except Exception:
            pass

        try:
            from apps.mail_engine.tasks import provision_domain_task
            provision_domain_task.delay(str(domain.id))
        except Exception:
            pass

        log_event(request.tenant, LogEventType.DOMAIN_ADDED, request=request, domain=domain)
        return Response(DomainSerializer(domain).data, status=201)


class DomainDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def _get_domain(self, request, pk):
        return Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()

    def get(self, request, pk):
        domain = self._get_domain(request, pk)
        if not domain:
            return Response({"detail": "Not found."}, status=404)
        return Response(DomainSerializer(domain).data)

    def delete(self, request, pk):
        domain = self._get_domain(request, pk)
        if not domain:
            return Response({"detail": "Not found."}, status=404)

        # Fire-and-forget deprovision in mail engine
        if domain.mail_engine_provisioned:
            try:
                from apps.mail_engine.tasks import deprovision_domain_task
                deprovision_domain_task.delay(domain.domain)
            except Exception:
                pass

        log_event(request.tenant, LogEventType.DOMAIN_DELETED, request=request, domain=domain)
        domain.delete()
        return Response(status=204)


class DomainProvisionView(APIView):
    """POST /api/domains/{id}/provision/ — manually re-trigger mail engine provisioning."""
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def post(self, request, pk):
        domain = Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()
        if not domain:
            return Response({"detail": "Not found."}, status=404)

        # Reset error so Celery task retries cleanly
        domain.mail_engine_provisioned = False
        domain.mail_engine_error = ""
        domain.save(update_fields=["mail_engine_provisioned", "mail_engine_error"])

        try:
            from apps.mail_engine.tasks import provision_domain_task
            provision_domain_task.delay(str(domain.id))
            return Response({"status": "queued"})
        except Exception as exc:
            logger.error("Failed to queue provision_domain_task: %s", exc)
            return Response({"detail": "Failed to queue provisioning task."}, status=503)
