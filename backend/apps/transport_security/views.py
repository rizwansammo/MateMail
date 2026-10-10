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
    DomainTransportSecurity, TransportSecurityCertificateStatus,
    TransportSecurityLifecycle, new_policy_id,
)
from .serializers import (
    TransportSecurityToggleSerializer, describe_transport_security,
    self_service_available_for,
)
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import Throttled
from apps.security import ratelimit
from apps.security.limits import DOMAIN_CHECK_PER_DOMAIN
from .dns import check_domain_policy_dns, check_publication_txt


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

        if not self_service_available_for(domain):
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
            if config.lifecycle in (
                TransportSecurityLifecycle.DEACTIVATING,
                TransportSecurityLifecycle.DRAINING,
            ):
                return Response({"detail": "Safe offboarding is underway. Wait for completion."}, status=409)
            if not config.enabled:
                # A fresh opted-in cycle needs a fresh version identifier.
                # Repeated enable calls do NOT rotate an existing one.
                config.enabled = True
                config.policy_id = new_policy_id()
                config.lifecycle = TransportSecurityLifecycle.PENDING_DNS
                config.last_error = ""
                config.deactivation_requested_at = None
                config.deactivation_policy_none_at = None
                config.deactivation_dns_absent_since = None
                config.deactivation_completed_at = None
                config.save(update_fields=[
                    "enabled", "policy_id", "lifecycle", "last_error",
                    "deactivation_requested_at", "deactivation_policy_none_at",
                    "deactivation_dns_absent_since", "deactivation_completed_at", "updated_at",
                ])
        else:
            if config.lifecycle in (
                TransportSecurityLifecycle.DEACTIVATING,
                TransportSecurityLifecycle.DRAINING,
            ):
                # Idempotent retirement request: do not reset the cache-drain timer.
                return Response(describe_transport_security(domain, config))
            if config.lifecycle in (TransportSecurityLifecycle.DISABLED,
                                    TransportSecurityLifecycle.PENDING_DNS):
                # The root worker cannot touch an unverified PENDING_DNS intent.
                if config.enabled:
                    config.enabled = False
                    config.lifecycle = TransportSecurityLifecycle.DISABLED
                    config.save(update_fields=["enabled", "lifecycle", "updated_at"])
            elif config.lifecycle in (
                TransportSecurityLifecycle.PROVISIONING,
                TransportSecurityLifecycle.READY,
                TransportSecurityLifecycle.ACTIVE,
                TransportSecurityLifecycle.ERROR,
            ):
                # Never drop HTTPS hosting or the original domain ownership row.
                # The separate root worker first serves mode:none, then waits
                # for TXT removal and a conservative cache-expiry period.
                config.lifecycle = TransportSecurityLifecycle.DEACTIVATING
                config.deactivation_requested_at = timezone.now()
                config.deactivation_policy_none_at = None
                config.deactivation_dns_absent_since = None
                config.last_error = ""
                config.save(update_fields=[
                    "lifecycle", "deactivation_requested_at", "deactivation_policy_none_at",
                    "deactivation_dns_absent_since", "last_error", "updated_at",
                ])
            else:
                return Response({"detail": "Unexpected lifecycle state; contact platform support."}, status=409)

        return Response(describe_transport_security(domain, config))

class DomainTransportSecurityDNSVerifyView(APIView):
    """Owner/Admin requested DNS verification. NEVER creates a certificate itself."""
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    @transaction.atomic
    def post(self, request, pk):
        domain = get_object_or_404(
            Domain.objects.for_tenant(request.tenant).select_for_update(), pk=pk
        )
        if not self_service_available_for(domain):
            return Response({"detail": "Advanced transport security is not available."}, status=503)
        try:
            assert_can_use_mail(request.tenant)
        except MailNotPermitted as exc:
            return Response({"detail": exc.customer_message}, status=403)

        row = DomainTransportSecurity.objects.filter(domain=domain, enabled=True).first()
        if row is None:
            return Response({"detail": "Enable advanced transport security first."}, status=409)
        if row.lifecycle not in (
            TransportSecurityLifecycle.PENDING_DNS,
            TransportSecurityLifecycle.ERROR,
            TransportSecurityLifecycle.READY,
            TransportSecurityLifecycle.ACTIVE,
        ):
            return Response({"detail": "Provisioning is still in progress."}, status=409)

        decision = ratelimit.hit(
            DOMAIN_CHECK_PER_DOMAIN.bucket,
            "transport-security:" + str(domain.id),
            limit=DOMAIN_CHECK_PER_DOMAIN.limit,
            window=DOMAIN_CHECK_PER_DOMAIN.window,
        )
        if not decision.allowed:
            raise Throttled(wait=decision.retry_after, detail="Too many DNS checks. Try later.")

        verified, message = check_domain_policy_dns(domain)
        if not verified:
            row.dns_verified_at = None
            fields = ["dns_verified_at", "updated_at"]
            if row.lifecycle in (TransportSecurityLifecycle.READY, TransportSecurityLifecycle.ACTIVE):
                # An existing policy remains hosted for remote MTA caches.
                # Withdraw the current verification claim, not the HTTPS site.
                row.dns_records_checked_at = timezone.now()
                row.sts_txt_verified_at = None
                row.tls_rpt_txt_verified_at = None
                row.lifecycle = TransportSecurityLifecycle.READY
                fields += ["dns_records_checked_at", "sts_txt_verified_at",
                           "tls_rpt_txt_verified_at", "lifecycle"]
            row.save(update_fields=fields)
            return Response({"verified": False, "detail": message}, status=409)

        now = timezone.now()
        if row.lifecycle in (TransportSecurityLifecycle.READY, TransportSecurityLifecycle.ACTIVE):
            if (row.certificate_status != TransportSecurityCertificateStatus.ACTIVE
                    or row.cert_verified_at is None):
                return Response({"verified": False, "detail": "HTTPS policy certificate is not verified."}, status=409)
            sts_ok, tls_ok = check_publication_txt(domain, row.policy_id)
            if sts_ok is None or tls_ok is None:
                return Response({"verified": False, "detail": "DNS lookup temporarily unavailable. Please retry."}, status=503)
            row.dns_verified_at = now
            row.dns_records_checked_at = now
            row.sts_txt_verified_at = now if sts_ok else None
            row.tls_rpt_txt_verified_at = now if tls_ok else None
            row.lifecycle = (TransportSecurityLifecycle.ACTIVE if sts_ok
                             else TransportSecurityLifecycle.READY)
            if sts_ok and row.activated_at is None:
                row.activated_at = now
            row.save(update_fields=[
                "dns_verified_at", "dns_records_checked_at", "sts_txt_verified_at",
                "tls_rpt_txt_verified_at", "lifecycle", "activated_at", "updated_at",
            ])
            return Response({
                "verified": True,
                "sts_txt_verified": sts_ok,
                "tls_rpt_txt_verified": tls_ok,
                "detail": (
                    "MTA-STS TXT verified." if sts_ok else
                    "HTTPS ready, but MTA-STS TXT is missing or incorrect."
                ) + (
                    " TLS reporting TXT verified." if tls_ok else
                    " TLS reporting TXT is not yet verified."
                ),
            })

        # Before HTTPS provisioning, only ownership, CNAME and MX are checked.
        row.dns_verified_at = now
        row.lifecycle = TransportSecurityLifecycle.PENDING_DNS
        row.last_error = ""
        row.save(update_fields=["dns_verified_at", "lifecycle", "last_error", "updated_at"])
        return Response({"verified": True, "detail": message})
