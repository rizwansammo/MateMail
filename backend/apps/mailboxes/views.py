import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.models import Domain
from apps.domains.verification import DomainNotVerified, assert_provisionable
from apps.mail_engine.errors import MailEngineError
from apps.billing.utils import (
    check_mailbox_limit,
    get_plan,
    reserve_resource_slot,
    resolve_mailbox_quota,
)
from apps.tenants.policy import MailNotPermitted, assert_can_use_mail
from apps.security import ratelimit
from apps.security.limits import MAILBOX_CREATE_PER_TENANT
from apps.logs.models import LogEventType
from apps.logs.utils import log_event
from apps.mail_directory.services import AddressConflict
from apps.tenants.permissions import IsEmailVerified, IsTenantAdmin, TenantReadAdminWrite
from .models import Mailbox
from .serializers import MailboxCreateSerializer, MailboxReProvisionSerializer, MailboxSerializer, MailboxStatusSerializer

logger = logging.getLogger(__name__)


def _provision_mailbox(mailbox, password: str) -> None:
    """
    Bring a mailbox to its desired state in the Mail Engine.

    The password stays in this call stack: it is passed to the adapter and never
    stored, queued or logged.

    Any failure is recorded as a MateMail-authored message. Raw engine text is
    logged and discarded — see errors.MailEngineError, whose str() is
    deliberately the customer message so this field cannot leak engine detail.
    """
    from apps.mail_engine.dto import MailboxSpec
    from apps.mail_engine.errors import MailEngineError
    from apps.mail_engine.factory import get_adapter

    spec = MailboxSpec.from_model(mailbox)
    try:
        get_adapter().ensure_mailbox(spec, password)
    except MailEngineError as exc:
        logger.error("Mailbox provisioning failed for %s: %s", mailbox.email, exc.log_message)
        mailbox.mail_engine_error = exc.customer_message
        mailbox.save(update_fields=["mail_engine_error"])
        raise

    mailbox.mail_engine_provisioned = True
    mailbox.mail_engine_error = ""
    mailbox.save(update_fields=["mail_engine_provisioned", "mail_engine_error"])


class MailboxListCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def get(self, request):
        mailboxes = Mailbox.objects.for_tenant(request.tenant).select_related("domain")
        return Response(MailboxSerializer(mailboxes, many=True).data)

    def post(self, request):
        # Plan limits cap the total; this caps the rate. A tenant whose plan
        # allows many mailboxes should still not be able to script thousands of
        # provisioning calls into the Mail Engine in a minute.
        decision = ratelimit.hit(
            MAILBOX_CREATE_PER_TENANT.bucket,
            str(request.tenant.id),
            limit=MAILBOX_CREATE_PER_TENANT.limit,
            window=MAILBOX_CREATE_PER_TENANT.window,
        )
        if not decision.allowed:
            raise Throttled(
                wait=decision.retry_after,
                detail=(
                    "Too many mailboxes created recently. Please wait a little "
                    "before creating more."
                ),
            )

        # GATE: same authoritative policy as every other provisioning path.
        try:
            assert_can_use_mail(request.tenant)
        except MailNotPermitted as exc:
            logger.info(
                "Mailbox creation refused for tenant %s: %s",
                request.tenant.id, exc.reason_code,
            )
            return Response({"detail": exc.customer_message}, status=403)

        serializer = MailboxCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        password = data["password"]  # captured here; never persisted

        domain = Domain.objects.for_tenant(request.tenant).filter(pk=data["domain_id"]).first()
        if not domain:
            return Response({"domain_id": "Domain not found in this workspace."}, status=400)

        # GATE: creating a mailbox provisions its domain into the engine as a
        # side effect, so the same ownership rule applies here.
        try:
            assert_provisionable(domain)
        except DomainNotVerified as exc:
            return Response({"domain_id": exc.customer_message}, status=409)

        if Mailbox.objects.for_tenant(request.tenant).filter(
            local_part=data["local_part"], domain=domain
        ).exists():
            return Response(
                {"local_part": "A mailbox with this address already exists."},
                status=400,
            )

        # ── Quota comes from the PLAN, never from the request ────────────────
        #
        # The serializer used to accept any `quota_mb` from the client with a
        # 10 GB default and no ceiling, so a workspace on a 1 GB trial could
        # ask for — and get — whatever it typed. Storage is a commercial limit;
        # the customer may choose within it, not beyond it.
        plan = get_plan(request.tenant)
        requested = data.get("quota_mb")
        quota_mb, quota_error = resolve_mailbox_quota(plan, requested)
        if quota_error:
            return Response({"quota_mb": quota_error}, status=400)

        # Cap check and row creation under the tenant lock — see
        # reserve_resource_slot. The engine call stays OUTSIDE the lock: it is a
        # network round-trip and must not be held across a row lock.
        with reserve_resource_slot(request.tenant, check_mailbox_limit) as slot:
            if not slot.allowed:
                return Response({"detail": slot.message}, status=402)

            try:
                mailbox = Mailbox.objects.create(
                    tenant=request.tenant,
                    domain=domain,
                    local_part=data["local_part"],
                    full_name=data["full_name"],
                    quota_mb=quota_mb,
                )
            except AddressConflict as exc:
                return Response({"local_part": exc.customer_message}, status=400)

        # Synchronous provisioning — password only lives in this call stack.
        # A provisioning failure does not fail mailbox creation: the record
        # exists and can be re-provisioned. _provision_mailbox has already
        # stored a MateMail-authored message describing the state.
        try:
            _provision_mailbox(mailbox, password)
        except MailEngineError:
            pass
        except Exception:
            # Unexpected fault: never let an arbitrary exception string reach a
            # customer-visible field. Log the traceback and store our own text.
            logger.exception("Unexpected error provisioning mailbox %s", mailbox.email)
            mailbox.mail_engine_error = MailEngineError.customer_message
            mailbox.save(update_fields=["mail_engine_error"])

        log_event(request.tenant, LogEventType.MAILBOX_CREATED, request=request, mailbox=mailbox)
        return Response(MailboxSerializer(mailbox).data, status=201)


class MailboxDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

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

        # Queue engine cleanup before deleting the local row.  If the mailbox
        # is provisioned and the broker cannot accept the cleanup task, fail
        # closed: deleting our only durable record would otherwise leave an
        # orphaned live mailbox in the Mail Engine with no reconciliation path.
        if mb.mail_engine_provisioned:
            try:
                from apps.mail_engine.tasks import deprovision_mailbox_task
                deprovision_mailbox_task.delay(mb.email)
            except Exception as exc:
                logger.error(
                    "Refusing to delete mailbox %s (tenant %s) because engine "
                    "deprovisioning could not be queued: %s",
                    mb.email,
                    request.tenant.id,
                    exc,
                )
                return Response(
                    {
                        "detail": (
                            "This mailbox could not be removed right now. "
                            "Nothing has been changed — please try again shortly."
                        )
                    },
                    status=503,
                )

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

        # Re-enabling a mailbox is a live mail operation.  Keep disable
        # available during suspension so an admin can reduce access, but never
        # let an inactive workspace reactivate service through this endpoint.
        if new_status == "active":
            try:
                assert_can_use_mail(request.tenant)
            except MailNotPermitted as exc:
                return Response({"detail": exc.customer_message}, status=403)

        # Sync to the Mail Engine before updating our own record.
        if mb.mail_engine_provisioned:
            from apps.mail_engine.errors import MailEngineError
            from apps.mail_engine.factory import get_adapter

            try:
                get_adapter().set_mailbox_active(mb.email, active=new_status == "active")
            except MailEngineError as exc:
                logger.warning("Mailbox status sync failed for %s: %s", mb.email, exc.log_message)
                return Response({"detail": exc.customer_message}, status=503)

        mb.status = new_status
        mb.save(update_fields=["status", "updated_at"])
        if new_status == "disabled":
            log_event(request.tenant, LogEventType.MAILBOX_DISABLED, request=request, mailbox=mb)
        return Response(MailboxSerializer(mb).data)


class MailboxReProvisionView(APIView):
    """POST /api/mailboxes/{id}/reprovision/ — re-provision with a new password."""
    permission_classes = [IsAuthenticated, IsTenantAdmin, IsEmailVerified]

    def post(self, request, pk):
        mb = Mailbox.objects.for_tenant(request.tenant).select_related("domain").filter(pk=pk).first()
        if not mb:
            return Response({"detail": "Not found."}, status=404)

        # GATE: re-provisioning talks to the engine too.
        try:
            assert_can_use_mail(request.tenant)
        except MailNotPermitted as exc:
            return Response({"detail": exc.customer_message}, status=403)

        try:
            assert_provisionable(mb.domain)
        except DomainNotVerified as exc:
            return Response({"detail": exc.customer_message}, status=409)

        serializer = MailboxReProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        password = serializer.validated_data["password"]

        try:
            _provision_mailbox(mb, password)
        except MailEngineError as exc:
            logger.error("Mailbox re-provisioning failed for %s: %s", mb.email, exc.log_message)
            return Response({"detail": exc.customer_message}, status=503)
        except Exception:
            logger.exception("Unexpected error re-provisioning mailbox %s", mb.email)
            return Response({"detail": MailEngineError.customer_message}, status=503)

        return Response(MailboxSerializer(mb).data)
