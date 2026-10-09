"""Tenant-scoped opt-in intent API. No mail/DNS/Nginx side effects in P4-C.B."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.models import Domain
from apps.tenants.permissions import IsEmailVerified, TenantReadAdminWrite
from apps.tenants.policy import MailNotPermitted, assert_can_use_mail

from .models import (
    DomainTransportSecurity, TransportSecurityLifecycle, new_policy_id,
)
from .serializers import (
    TransportSecurityToggleSerializer, describe_transport_security,
)
from django.conf import settings


class DomainTransportSecurityView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def _domain(self, request, pk):
        return get_object_or_404(Domain.objects.for_tenant(request.tenant), pk=pk)

    def get(self, request, pk):
        domain = self._domain(request, pk)
        config = DomainTransportSecurity.objects.filter(domain=domain).first()
        return Response(describe_transport_security(domain, config))

    @transaction.atomic
    def post(self, request, pk):
        # Lock the actual domain to serialize concurrent first-time opt-ins.
        domain = get_object_or_404(
            Domain.objects.for_tenant(request.tenant).select_for_update(), pk=pk,
        )
        serializer = TransportSecurityToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enabled = serializer.validated_data["enabled"]

        if not getattr(settings, "TRANSPORT_SECURITY_SELF_SERVICE_ENABLED", False):
            return Response({
                "detail": "Advanced transport security configuration is not yet available."
            }, status=503)

        if enabled:
            try:
                assert_can_use_mail(request.tenant)
            except MailNotPermitted as exc:
                return Response({"detail": exc.customer_message}, status=403)
            if not domain.is_ownership_verified:
                return Response({
                    "detail": "Verify domain ownership before enabling transport security."
                }, status=409)

        config = DomainTransportSecurity.objects.filter(domain=domain).first()
        if not enabled and config is None:
            # Idempotent no-op: do not make DB rows for untouched domains.
            return Response(describe_transport_security(domain))

        if config is None:
            config = DomainTransportSecurity.objects.create(
                domain=domain, enabled=True,
                lifecycle=TransportSecurityLifecycle.PENDING_DNS,
            )
        elif enabled:
            if not config.enabled:
                # A fresh opted-in cycle needs a fresh version identifier.
                # Repeated enable calls do NOT rotate an existing one.
                config.enabled = True
                config.policy_id = new_policy_id()
                config.lifecycle = TransportSecurityLifecycle.PENDING_DNS
                config.last_error = ""
                config.save(update_fields=[
                    "enabled", "policy_id", "lifecycle", "last_error", "updated_at",
                ])
        else:
            # DNS policies can be cached and HTTPS hostnames may still exist:
            # P4-C.F will coordinate deactivation with the root-owned worker.
            if config.lifecycle not in (
                TransportSecurityLifecycle.DISABLED,
                TransportSecurityLifecycle.PENDING_DNS,
            ):
                return Response({
                    "detail": "This domain has edge provisioning state. "
                              "Managed removal is required before disabling transport security."
                }, status=409)
            if config.enabled:
                config.enabled = False
                config.lifecycle = TransportSecurityLifecycle.DISABLED
                config.save(update_fields=["enabled", "lifecycle", "updated_at"])

        return Response(describe_transport_security(domain, config))
