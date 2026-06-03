import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.models import Domain
from apps.billing.utils import check_mailbox_limit
from apps.logs.models import LogEventType
from apps.logs.utils import log_event
from apps.tenants.permissions import HasTenantAccess, IsTenantAdmin
from .models import Mailbox
from .serializers import MailboxCreateSerializer, MailboxReProvisionSerializer, MailboxSerializer, MailboxStatusSerializer

logger = logging.getLogger(__name__)


def _provision_mailbox(mailbox, password: str) -> None:
    """Sync provisioning helper — password stays in memory, never stored."""
    from apps.mail_engine.factory import get_adapter
    adapter = get_adapter()
    result = adapter.provision_mailbox(mailbox, password)
    if result.success:
        mailbox.mail_engine_provisioned = True
        mailbox.mail_engine_error = ""
    else:
        mailbox.mail_engine_error = result.message[:500]
    mailbox.save(update_fields=["mail_engine_provisioned", "mail_engine_error"])


class MailboxListCreateView(APIView):
    permission_classes = [IsAuthenticated, HasTenantAccess]

    def get(self, request):
        mailboxes = Mailbox.objects.for_tenant(request.tenant).select_related("domain")
        return Response(MailboxSerializer(mailboxes, many=True).data)

    def post(self, request):
        allowed, msg = check_mailbox_limit(request.tenant)
        if not allowed:
            return Response({"detail": msg}, status=402)

        serializer = MailboxCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        password = data["password"]  # captured here; never persisted

        domain = Domain.objects.for_tenant(request.tenant).filter(pk=data["domain_id"]).first()
        if not domain:
            return Response({"domain_id": "Domain not found in this workspace."}, status=400)

        if Mailbox.objects.for_tenant(request.tenant).filter(
            local_part=data["local_part"], domain=domain
        ).exists():
            return Response(
                {"local_part": "A mailbox with this address already exists."},
                status=400,
            )

        mailbox = Mailbox.objects.create(
            tenant=request.tenant,
            domain=domain,
            local_part=data["local_part"],
            full_name=data["full_name"],
            quota_mb=data["quota_mb"],
        )

        # Synchronous provisioning — password only lives in this call stack
        try:
            _provision_mailbox(mailbox, password)
        except Exception as exc:
            logger.error("Mailbox provision error for %s: %s", mailbox.email, exc)
            mailbox.mail_engine_error = str(exc)[:500]
            mailbox.save(update_fields=["mail_engine_error"])

        log_event(request.tenant, LogEventType.MAILBOX_CREATED, request=request, mailbox=mailbox)
        return Response(MailboxSerializer(mailbox).data, status=201)


class MailboxDetailView(APIView):
    permission_classes = [IsAuthenticated, HasTenantAccess]

    def _get_mailbox(self, request, pk):
        return Mailbox.objects.for_tenant(request.tenant).select_related("domain").filter(pk=pk).first()

    def get(self, request, pk):
        mb = self._get_mailbox(request, pk)
        if not mb:
            return Response({"detail": "Not found."}, status=404)
        return Response(MailboxSerializer(mb).data)

    def delete(self, request, pk):
        mb = self._get_mailbox(request, pk)
        if not mb:
            return Response({"detail": "Not found."}, status=404)

        # Fire-and-forget deprovision
        if mb.mail_engine_provisioned:
            try:
                from apps.mail_engine.tasks import deprovision_mailbox_task
                deprovision_mailbox_task.delay(mb.email)
            except Exception:
                pass

        log_event(request.tenant, LogEventType.MAILBOX_DELETED, request=request, mailbox=mb)
        mb.delete()
        return Response(status=204)


class MailboxStatusView(APIView):
    """PATCH /api/mailboxes/{id}/status/ — enable or disable a mailbox (syncs to mail engine)."""
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def patch(self, request, pk):
        mb = Mailbox.objects.for_tenant(request.tenant).select_related("domain").filter(pk=pk).first()
        if not mb:
            return Response({"detail": "Not found."}, status=404)

        if mb.status == "suspended":
            return Response({"detail": "Cannot change status of a suspended mailbox."}, status=400)

        serializer = MailboxStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        if mb.status == new_status:
            return Response(MailboxSerializer(mb).data)

        # Sync to mail engine before updating DB
        if mb.mail_engine_provisioned:
            from apps.mail_engine.factory import get_adapter
            adapter = get_adapter()
            if new_status == "disabled":
                result = adapter.disable_mailbox(mb)
            else:
                result = adapter.enable_mailbox(mb)
            if not result.success:
                logger.warning("Mail engine status sync failed for %s: %s", mb.email, result.message)

        mb.status = new_status
        mb.save(update_fields=["status", "updated_at"])
        if new_status == "disabled":
            log_event(request.tenant, LogEventType.MAILBOX_DISABLED, request=request, mailbox=mb)
        return Response(MailboxSerializer(mb).data)


class MailboxReProvisionView(APIView):
    """POST /api/mailboxes/{id}/reprovision/ — re-provision with a new password."""
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def post(self, request, pk):
        mb = Mailbox.objects.for_tenant(request.tenant).select_related("domain").filter(pk=pk).first()
        if not mb:
            return Response({"detail": "Not found."}, status=404)

        serializer = MailboxReProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        password = serializer.validated_data["password"]

        try:
            _provision_mailbox(mb, password)
        except Exception as exc:
            logger.error("Re-provision error for %s: %s", mb.email, exc)
            return Response({"detail": "Provisioning failed. Check mail_engine_error."}, status=503)

        return Response(MailboxSerializer(mb).data)
