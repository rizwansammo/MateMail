from __future__ import annotations

import hmac
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.logs.models import LogEventType
from apps.logs.utils import log_event
from apps.security import ratelimit
from apps.security.limits import CUSTOM_HOST_CHECK_PER_HOST
from apps.tenants.permissions import IsEmailVerified, TenantReadAdminWrite
from apps.tenants.policy import MailNotPermitted, assert_can_use_mail

from .custom_host_serializers import (
    CustomHostnameCreateSerializer,
    CustomHostnameInternalStateSerializer,
    CustomHostnameSerializer,
)
from .custom_hosts import (
    CustomHostnameValueError,
    cname_target,
    invalidate_custom_hostname_cache,
    normalize_hostname,
    verify_custom_hostname_dns,
)
from .models import (
    CUSTOM_HOSTNAME_LIVE_STATES,
    CustomHostname,
    CustomHostnameCertificateStatus,
    CustomHostnameDNSStatus,
    CustomHostnameProvisioningStatus,
)

logger = logging.getLogger(__name__)


def _change_allowed(tenant):
    try:
        assert_can_use_mail(tenant)
    except MailNotPermitted as exc:
        return Response({"detail": exc.customer_message}, status=403)
    return None


def _live_for_tenant(tenant):
    return CustomHostname.objects.filter(
        tenant=tenant,
        provisioning_status__in=CUSTOM_HOSTNAME_LIVE_STATES,
    )


def _enforce_dns_check_limit(custom_hostname: CustomHostname) -> None:
    decision = ratelimit.hit(
        CUSTOM_HOST_CHECK_PER_HOST.bucket,
        str(custom_hostname.id),
        limit=CUSTOM_HOST_CHECK_PER_HOST.limit,
        window=CUSTOM_HOST_CHECK_PER_HOST.window,
    )
    if not decision.allowed:
        raise Throttled(
            wait=decision.retry_after,
            detail=(
                "This hostname has been checked too many times recently. "
                "DNS changes can take a while to propagate — please wait before checking again."
            ),
        )


class CustomHostnameListCreateView(APIView):
    """
    GET/POST /api/custom-hostnames/

    One live hostname per surface per tenant. Inactive rows are retained as
    history but are not returned to the customer setup screen.
    """

    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def get(self, request):
        rows = _live_for_tenant(request.tenant).order_by("surface", "created_at")
        return Response(CustomHostnameSerializer(rows, many=True).data)

    def post(self, request):
        denied = _change_allowed(request.tenant)
        if denied:
            return denied

        serializer = CustomHostnameCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        hostname = serializer.validated_data["hostname"]
        surface = serializer.validated_data["surface"]

        try:
            with transaction.atomic():
                # Lock the tenant so two admins cannot both consume the same
                # one-host-per-surface slot concurrently.
                from .models import Tenant

                Tenant.objects.select_for_update().get(pk=request.tenant.pk)

                if _live_for_tenant(request.tenant).filter(surface=surface).exists():
                    return Response(
                        {
                            "detail": (
                                f"This organization already has a custom {surface} hostname. "
                                "Remove it before adding another."
                            )
                        },
                        status=409,
                    )

                if CustomHostname.objects.filter(
                    hostname=hostname,
                    provisioning_status__in=CUSTOM_HOSTNAME_LIVE_STATES,
                ).exists():
                    return Response(
                        {"hostname": ["This hostname is already configured."]},
                        status=409,
                    )

                row = CustomHostname.objects.create(
                    tenant=request.tenant,
                    hostname=hostname,
                    surface=surface,
                )
        except IntegrityError:
            # The partial unique constraints are the concurrency backstop.
            return Response(
                {"detail": "That custom hostname or surface is already in use."},
                status=409,
            )

        log_event(
            request.tenant,
            LogEventType.CUSTOM_HOSTNAME_ADDED,
            request=request,
            metadata={"hostname": hostname, "surface": surface},
        )
        return Response(CustomHostnameSerializer(row).data, status=201)


class CustomHostnameDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def _get(self, request, pk):
        return _live_for_tenant(request.tenant).filter(pk=pk).first()

    def get(self, request, pk):
        row = self._get(request, pk)
        if not row:
            return Response({"detail": "Not found."}, status=404)
        return Response(CustomHostnameSerializer(row).data)

    def delete(self, request, pk):
        # Removal is always permitted to an authorized tenant admin. A
        # suspended/past-due organization may not add or verify new hostnames,
        # but it must still be able to relinquish one it already controls.
        row = self._get(request, pk)
        if not row:
            return Response({"detail": "Not found."}, status=404)

        # Phase 2 can safely retire rows that have never reached the edge. Once
        # Phase 3 has begun provisioning, removal must coordinate nginx and
        # certificate cleanup rather than pretending a database update did it.
        if row.provisioning_status in {
            CustomHostnameProvisioningStatus.PROVISIONING,
            CustomHostnameProvisioningStatus.READY,
            CustomHostnameProvisioningStatus.ACTIVE,
            CustomHostnameProvisioningStatus.DEACTIVATING,
        } or row.certificate_status in {
            CustomHostnameCertificateStatus.ISSUING,
            CustomHostnameCertificateStatus.ACTIVE,
        }:
            return Response(
                {
                    "detail": (
                        "This hostname already has edge provisioning state and "
                        "must be deactivated by the provisioning service."
                    )
                },
                status=409,
            )

        hostname = row.hostname
        row.provisioning_status = CustomHostnameProvisioningStatus.INACTIVE
        row.deactivated_at = timezone.now()
        row.last_error = ""
        row.save(
            update_fields=[
                "provisioning_status",
                "deactivated_at",
                "last_error",
                "updated_at",
            ]
        )
        invalidate_custom_hostname_cache(hostname)

        log_event(
            request.tenant,
            LogEventType.CUSTOM_HOSTNAME_REMOVED,
            request=request,
            metadata={"hostname": hostname, "surface": row.surface},
        )
        return Response(status=204)


class CustomHostnameVerifyView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def post(self, request, pk):
        denied = _change_allowed(request.tenant)
        if denied:
            return denied

        row = _live_for_tenant(request.tenant).filter(pk=pk).first()
        if not row:
            return Response({"detail": "Not found."}, status=404)

        _enforce_dns_check_limit(row)
        verified, message = verify_custom_hostname_dns(row)

        log_event(
            request.tenant,
            (
                LogEventType.CUSTOM_HOSTNAME_VERIFIED
                if verified
                else LogEventType.CUSTOM_HOSTNAME_VERIFY_FAILED
            ),
            result="success" if verified else "failed",
            request=request,
            metadata={"hostname": row.hostname, "surface": row.surface},
        )

        return Response(
            {
                "verified": verified,
                "detail": message,
                "custom_hostname": CustomHostnameSerializer(row).data,
            },
            status=200 if verified else 409,
        )


# ── Internal provisioning contract ───────────────────────────────────────────


def _provisioner_authorized(request) -> bool:
    expected = getattr(settings, "CUSTOM_HOST_PROVISIONER_SECRET", "")
    if not expected:
        logger.error(
            "CUSTOM_HOST_PROVISIONER_SECRET is not configured — rejecting custom-host internal call"
        )
        return False
    provided = request.META.get("HTTP_X_MATEMAIL_CUSTOM_HOST_SECRET", "")
    return hmac.compare_digest(provided.encode(), expected.encode())


def _internal_denied(request):
    if _provisioner_authorized(request):
        return None
    return Response({"detail": "Unauthorized"}, status=403)


class CustomHostnamePendingInternalView(APIView):
    """
    GET /api/internal/custom-hostnames/pending/

    Polled by the Phase 3 root-owned host worker. The database row is the durable
    job; Django does not execute host commands and needs no host privilege.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        denied = _internal_denied(request)
        if denied:
            return denied

        rows = (
            CustomHostname.objects.filter(
                dns_status=CustomHostnameDNSStatus.VERIFIED,
                provisioning_status=CustomHostnameProvisioningStatus.UNPROVISIONED,
                tenant__status__in=("trial", "active"),
                tenant__approved_at__isnull=False,
            )
            .select_related("tenant")
            .order_by("created_at")[:50]
        )
        return Response(
            {
                "results": [
                    {
                        "id": str(row.id),
                        "hostname": row.hostname,
                        "surface": row.surface,
                        "tenant_id": str(row.tenant_id),
                        "cname_target": cname_target(),
                    }
                    for row in rows
                ]
            }
        )


class CustomHostnameAuthorizeInternalView(APIView):
    """
    POST /api/internal/custom-hostnames/authorize/

    A narrow approval query for the host worker. A hostname that is not both
    tenant-owned in MateMail and DNS-verified is never approved for certificate
    provisioning.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        denied = _internal_denied(request)
        if denied:
            return denied

        try:
            hostname = normalize_hostname(request.data.get("hostname", ""))
        except CustomHostnameValueError:
            return Response({"approved": False}, status=404)

        row = (
            CustomHostname.objects.filter(
                hostname=hostname,
                dns_status=CustomHostnameDNSStatus.VERIFIED,
                provisioning_status__in=(
                    CustomHostnameProvisioningStatus.UNPROVISIONED,
                    CustomHostnameProvisioningStatus.PROVISIONING,
                    CustomHostnameProvisioningStatus.READY,
                    CustomHostnameProvisioningStatus.ACTIVE,
                    CustomHostnameProvisioningStatus.ERROR,
                ),
            )
            .select_related("tenant")
            .first()
        )
        if not row or not row.tenant.can_use_mail:
            return Response({"approved": False}, status=404)

        return Response(
            {
                "approved": True,
                "id": str(row.id),
                "hostname": row.hostname,
                "surface": row.surface,
                "tenant_id": str(row.tenant_id),
            }
        )


class CustomHostnameStateInternalView(APIView):
    """
    POST /api/internal/custom-hostnames/<id>/state/

    Phase 3's host worker reports only edge preparation states. It cannot mark a
    hostname ACTIVE; Phase 4 owns that transition after application routing and
    tenant-binding are safe.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    _TRANSITIONS = {
        CustomHostnameProvisioningStatus.UNPROVISIONED: {
            CustomHostnameProvisioningStatus.PROVISIONING,
            CustomHostnameProvisioningStatus.ERROR,
        },
        CustomHostnameProvisioningStatus.ERROR: {
            CustomHostnameProvisioningStatus.PROVISIONING,
            CustomHostnameProvisioningStatus.ERROR,
        },
        CustomHostnameProvisioningStatus.PROVISIONING: {
            CustomHostnameProvisioningStatus.PROVISIONING,
            CustomHostnameProvisioningStatus.READY,
            CustomHostnameProvisioningStatus.ERROR,
        },
        CustomHostnameProvisioningStatus.READY: {
            CustomHostnameProvisioningStatus.READY,
            CustomHostnameProvisioningStatus.ERROR,
        },
    }

    def post(self, request, pk):
        denied = _internal_denied(request)
        if denied:
            return denied

        serializer = CustomHostnameInternalStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            row = (
                CustomHostname.objects.select_for_update()
                .select_related("tenant")
                .filter(pk=pk)
                .first()
            )
            if not row:
                return Response({"detail": "Not found."}, status=404)
            if not row.tenant.can_use_mail:
                return Response(
                    {"detail": "This organization is not eligible for custom-host provisioning."},
                    status=409,
                )
            if not row.is_dns_verified:
                return Response(
                    {"detail": "DNS verification is required before provisioning."},
                    status=409,
                )

            next_status = data["provisioning_status"]
            allowed = self._TRANSITIONS.get(row.provisioning_status, set())
            if next_status not in allowed:
                return Response(
                    {
                        "detail": (
                            f"Invalid provisioning transition: "
                            f"{row.provisioning_status} -> {next_status}."
                        )
                    },
                    status=409,
                )

            row.provisioning_status = next_status
            update_fields = ["provisioning_status", "updated_at"]

            if "certificate_status" in data:
                row.certificate_status = data["certificate_status"]
                update_fields.append("certificate_status")

            if next_status == CustomHostnameProvisioningStatus.ERROR:
                row.last_error = data.get("last_error", "") or (
                    "HTTPS provisioning could not be completed. Please try again."
                )
                update_fields.append("last_error")
            elif next_status == CustomHostnameProvisioningStatus.READY:
                row.last_error = ""
                update_fields.append("last_error")

            row.save(update_fields=update_fields)

        invalidate_custom_hostname_cache(row.hostname)
        return Response(CustomHostnameSerializer(row).data)
