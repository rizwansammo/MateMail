"""Purpose-scoped root-worker contract; /api/internal blocked by the public edge."""
import hmac
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.models import DomainOwnership
from apps.tenants.policy import mail_denial_reason

from .dns import check_domain_policy_dns, policy_hostname, validated_edge
from .models import (
    DomainTransportSecurity, TransportSecurityCertificateStatus,
    TransportSecurityLifecycle,
)


def authorized(request):
    secret = getattr(settings, "TRANSPORT_SECURITY_PROVISIONER_SECRET", "")
    supplied = request.META.get("HTTP_X_MATEMAIL_TRANSPORT_SECRET", "")
    return bool(secret and supplied and hmac.compare_digest(secret, supplied))


def denied(request):
    if not getattr(settings, "TRANSPORT_SECURITY_PROVISIONING_ENABLED", False):
        return Response({"detail": "Unavailable"}, status=503)
    if not authorized(request):
        return Response({"detail": "Unauthorized"}, status=403)
    return None


def row_allowed(row):
    if not (
        row.enabled and row.domain.is_ownership_verified
        and row.domain.ownership_status == DomainOwnership.VERIFIED
        and not mail_denial_reason(row.domain.tenant)
    ):
        return False
    ok, _ = check_domain_policy_dns(row.domain)
    return ok


def job(row):
    return {
        "id": str(row.domain_id),
        "domain": row.domain.domain,
        "hostname": policy_hostname(row.domain.domain),
        "tenant_id": str(row.domain.tenant_id),
        "policy_id": row.policy_id,
        "mx": getattr(settings, "MAIL_HOSTNAME", "mx.matemail.pro").lower().rstrip("."),
        "edge": validated_edge(),
    }


class PendingTransportSecurityView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        reject = denied(request)
        if reject is not None:
            return reject
        # Filter with durable explicit DNS verification; authorization below
        # rechecks actual public records again immediately before host mutation.
        rows = DomainTransportSecurity.objects.filter(
            enabled=True, dns_verified_at__isnull=False,
            lifecycle__in=(
                TransportSecurityLifecycle.PENDING_DNS,
                TransportSecurityLifecycle.PROVISIONING,
            ),
            domain__ownership_status=DomainOwnership.VERIFIED,
            domain__tenant__status__in=("trial", "active"),
            domain__tenant__approved_at__isnull=False,
        ).select_related("domain", "domain__tenant").order_by("created_at")[:25]
        results = []
        for row in rows:
            try:
                if row_allowed(row):
                    results.append(job(row))
            except ValueError:
                continue
        return Response({"results": results})


class AuthorizeTransportSecurityView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        reject = denied(request)
        if reject is not None:
            return reject
        try:
            domain_id = uuid.UUID(str(request.data.get("id", "")))
        except (ValueError, TypeError, AttributeError):
            return Response({"approved": False}, status=404)
        row = DomainTransportSecurity.objects.select_related(
            "domain", "domain__tenant"
        ).filter(
            domain_id=domain_id, enabled=True,
            dns_verified_at__isnull=False,
            lifecycle__in=(
                TransportSecurityLifecycle.PENDING_DNS,
                TransportSecurityLifecycle.PROVISIONING,
            ),
        ).first()
        if row is None or not row_allowed(row):
            return Response({"approved": False}, status=404)
        return Response({"approved": True, **job(row)})


class StateTransportSecurityView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @transaction.atomic
    def post(self, request, pk):
        reject = denied(request)
        if reject is not None:
            return reject
        if set(request.data) != {"lifecycle", "certificate_status", "policy_id"}:
            return Response({"detail": "Invalid state payload."}, status=400)
        lifecycle = request.data["lifecycle"]
        certificate = request.data["certificate_status"]
        valid = {
            TransportSecurityLifecycle.PENDING_DNS: {TransportSecurityLifecycle.PROVISIONING},
            TransportSecurityLifecycle.PROVISIONING: {
                TransportSecurityLifecycle.PROVISIONING,
                TransportSecurityLifecycle.READY,
                TransportSecurityLifecycle.ERROR,
            },
            TransportSecurityLifecycle.ERROR: {TransportSecurityLifecycle.PROVISIONING},
        }
        allowed_certs = {
            TransportSecurityLifecycle.PROVISIONING: {
                TransportSecurityCertificateStatus.NOT_REQUESTED,
                TransportSecurityCertificateStatus.ISSUING,
                TransportSecurityCertificateStatus.ACTIVE,
            },
            TransportSecurityLifecycle.READY: {TransportSecurityCertificateStatus.ACTIVE},
            TransportSecurityLifecycle.ERROR: {TransportSecurityCertificateStatus.ERROR},
        }
        row = DomainTransportSecurity.objects.select_for_update().select_related(
            "domain", "domain__tenant"
        ).filter(domain_id=pk).first()
        if not row:
            return Response({"detail": "Not found."}, status=404)
        if (
            not row.enabled
            or not row.domain.is_ownership_verified
            or mail_denial_reason(row.domain.tenant)
            or request.data["policy_id"] != row.policy_id
            or lifecycle not in valid.get(row.lifecycle, set())
            or certificate not in allowed_certs.get(lifecycle, set())
        ):
            return Response({"detail": "State transition refused."}, status=409)
        if lifecycle == TransportSecurityLifecycle.READY and not row.dns_verified_at:
            return Response({"detail": "DNS verification required."}, status=409)
        row.lifecycle = lifecycle
        row.certificate_status = certificate
        row.last_error = (
            "HTTPS policy provisioning failed; verify DNS and retry."
            if lifecycle == TransportSecurityLifecycle.ERROR else ""
        )
        fields = ["lifecycle", "certificate_status", "last_error", "updated_at"]
        if lifecycle == TransportSecurityLifecycle.READY:
            row.cert_verified_at = timezone.now()
            fields.append("cert_verified_at")
        row.save(update_fields=fields)
        return Response({"updated": True, "lifecycle": lifecycle})
