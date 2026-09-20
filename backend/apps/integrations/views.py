import hashlib
import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.mailboxes.models import Mailbox
from apps.postbox import imap, sending
from apps.postbox.models import MailSignature
from apps.postbox.views_compose import ComposeMixin, ComposeSerializer
from apps.security import ratelimit
from apps.security.limits import POSTBOX_SEND_PER_MAILBOX
from apps.tenants.models import MemberRole, MemberStatus, TenantMembership
from apps.tenants.permissions import IsTenantAdmin

from .authentication import IntegrationAccessAuthentication
from .models import (
    AccessToken,
    ConnectionRequest,
    Integration,
    IntegrationDelivery,
    PERMISSION_LABELS,
)
from .security import mailbox_verification_requirement, verify_fresh_authorization
from .serializers import (
    AuthorizationSerializer,
    ConnectStartSerializer,
    ConnectStatusSerializer,
    IntegrationCreateSerializer,
)

logger = logging.getLogger(__name__)


def _hash(raw):
    return hashlib.sha256((raw or "").encode()).hexdigest()


def _integration_payload(integration):
    return {
        "id": str(integration.id),
        "name": integration.name,
        "tenant_id": str(integration.tenant_id),
        "organization": integration.tenant.name,
        "mailbox_id": str(integration.mailbox_id),
        "mailbox_email": integration.mailbox.email,
        "mailbox_name": integration.mailbox.full_name,
        "permissions": [
            {"key": key, "label": PERMISSION_LABELS[key]}
            for key in integration.permissions
            if key in PERMISSION_LABELS
        ],
        "created_at": integration.created_at,
        "last_used_at": integration.last_used_at,
        "active": integration.is_active,
    }


def _integration_from_secret(tenant_id, raw_secret):
    return (
        Integration.objects.select_related("tenant", "mailbox")
        .filter(
            tenant_id=tenant_id,
            secret_hash=_hash(raw_secret),
            revoked_at__isnull=True,
        )
        .first()
    )


class IntegrationListView(APIView):
    permission_classes = [IsTenantAdmin]

    def get(self, request):
        items = (
            Integration.objects.select_related("tenant", "mailbox")
            .filter(tenant=request.tenant)
        )
        return Response([_integration_payload(item) for item in items])

    def post(self, request):
        serializer = IntegrationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        mailbox = Mailbox.objects.filter(
            pk=data["mailbox_id"], tenant=request.tenant
        ).first()
        if mailbox is None:
            return Response(
                {"detail": "Mailbox not found in this workspace."}, status=404
            )
        if mailbox.status != "active" or not mailbox.mail_engine_provisioned:
            return Response(
                {"detail": "Choose an active, provisioned mailbox."}, status=400
            )

        raw, integration = Integration.issue(
            tenant=request.tenant,
            mailbox=mailbox,
            name=data["name"].strip(),
            created_by=request.user,
            permissions=data["permissions"],
        )
        payload = _integration_payload(integration)
        payload["integration_secret"] = raw
        payload["secret_notice"] = (
            "Copy this secret now. MateMail will not show it again."
        )
        return Response(payload, status=201)


class IntegrationDetailView(APIView):
    permission_classes = [IsTenantAdmin]

    def delete(self, request, integration_id):
        integration = Integration.objects.filter(
            pk=integration_id,
            tenant=request.tenant,
            revoked_at__isnull=True,
        ).first()
        if integration is None:
            return Response({"detail": "Not found."}, status=404)
        now = timezone.now()
        integration.revoked_at = now
        integration.save(update_fields=["revoked_at"])
        AccessToken.objects.filter(
            integration=integration, revoked_at__isnull=True
        ).update(revoked_at=now)
        return Response(status=204)


class ConnectStartView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ConnectStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        integration = _integration_from_secret(
            data["tenant_id"], data["integration_secret"]
        )
        if integration is None:
            return Response(
                {"detail": "Tenant ID or integration secret is not valid."},
                status=401,
            )
        if (
            integration.mailbox.status != "active"
            or not integration.mailbox.mail_engine_provisioned
        ):
            return Response(
                {"detail": "The connected mailbox is not available."},
                status=409,
            )

        raw_request, raw_poll, pending = ConnectionRequest.issue(integration)
        return Response(
            {
                **_integration_payload(integration),
                "request_token": raw_request,
                "poll_token": raw_poll,
                "authorization_url": (
                    f"{settings.APP_BASE_URL.rstrip('/')}"
                    f"/app/integrations/authorize?request={raw_request}"
                ),
                "expires_at": pending.expires_at,
            }
        )


class ConnectStatusView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ConnectStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        pending = (
            ConnectionRequest.objects.select_related(
                "integration__tenant", "integration__mailbox"
            )
            .filter(
                integration__tenant_id=data["tenant_id"],
                poll_hash=_hash(data["poll_token"]),
                integration__revoked_at__isnull=True,
            )
            .first()
        )
        if pending is None:
            return Response(
                {"detail": "Connection request not found."}, status=404
            )
        if pending.is_expired:
            return Response({"status": "expired"})
        if pending.rejected_at:
            return Response({"status": "rejected"})
        if not pending.approved_at:
            return Response({"status": "pending"})

        if pending.consumed_at:
            return Response(
                {
                    "status": "approved",
                    "access_token": None,
                    "already_issued": True,
                }
            )

        raw_access, _ = AccessToken.issue(pending.integration)
        pending.consumed_at = timezone.now()
        pending.save(update_fields=["consumed_at"])
        return Response(
            {
                "status": "approved",
                "access_token": raw_access,
                **_integration_payload(pending.integration),
            }
        )


def _pending_for_portal(request, raw_request):
    pending = (
        ConnectionRequest.objects.select_related(
            "integration__tenant", "integration__mailbox"
        )
        .filter(request_hash=_hash(raw_request))
        .first()
    )
    if pending is None or pending.is_expired:
        return None, Response(
            {"detail": "This connection request has expired."}, status=404
        )

    membership = TenantMembership.objects.filter(
        tenant=pending.integration.tenant,
        user=request.user,
        status=MemberStatus.ACTIVE,
        role__in=(MemberRole.OWNER, MemberRole.ADMIN),
    ).first()
    if membership is None:
        return None, Response(
            {
                "detail": (
                    "Only an owner or admin of this workspace can approve "
                    "this connection."
                )
            },
            status=403,
        )
    return pending, None


class AuthorizationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        raw_request = request.query_params.get("request", "")
        pending, error = _pending_for_portal(request, raw_request)
        if error:
            return error
        integration = pending.integration
        requirement = mailbox_verification_requirement(integration.mailbox)
        return Response(
            {
                **_integration_payload(integration),
                "request_status": (
                    "approved"
                    if pending.approved_at
                    else "rejected"
                    if pending.rejected_at
                    else "pending"
                ),
                "user_2fa_required": bool(request.user.two_factor_enabled),
                "mailbox_verification_required": requirement["required"],
                "mailbox_verification_label": requirement["label"],
                "expires_at": pending.expires_at,
            }
        )

    def post(self, request):
        raw_request = request.query_params.get("request", "")
        pending, error = _pending_for_portal(request, raw_request)
        if error:
            return error
        if pending.approved_at or pending.rejected_at:
            return Response(
                {"detail": "This connection request has already been decided."},
                status=409,
            )

        serializer = AuthorizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            verify_fresh_authorization(
                user=request.user,
                integration=pending.integration,
                **serializer.validated_data,
            )
        except DjangoValidationError as exc:
            detail = getattr(exc, "message_dict", None) or {
                "detail": exc.messages
            }
            raise ValidationError(detail)

        pending.approved_at = timezone.now()
        pending.approved_by = request.user
        pending.save(update_fields=["approved_at", "approved_by"])
        return Response({"status": "approved"})


class IntegrationProfileView(APIView):
    authentication_classes = [IntegrationAccessAuthentication]
    permission_classes = []

    def get(self, request):
        return Response(_integration_payload(request.integration))


class SignatureListView(APIView):
    authentication_classes = [IntegrationAccessAuthentication]
    permission_classes = []

    def get(self, request):
        integration = request.integration
        if not integration.has_permission("use_signatures"):
            return Response(
                {"detail": "This connection cannot use signatures."}, status=403
            )
        signatures = MailSignature.objects.for_mailbox(
            integration.mailbox
        ).order_by("name")
        return Response(
            {
                "results": [
                    {
                        "id": str(item.id),
                        "name": item.name,
                        "use_for_new": item.use_for_new,
                        "use_for_replies": item.use_for_replies,
                    }
                    for item in signatures
                ]
            }
        )


class IntegrationSendView(APIView, ComposeMixin):
    authentication_classes = [IntegrationAccessAuthentication]
    permission_classes = []

    def post(self, request):
        integration = request.integration
        if not integration.has_permission("send_email"):
            return Response(
                {"detail": "This connection cannot send email."}, status=403
            )

        idempotency_key = str(request.data.get("idempotency_key") or "").strip()
        if not idempotency_key:
            return Response({"detail": "idempotency_key is required."}, status=400)
        if len(idempotency_key) > 100:
            return Response({"detail": "idempotency_key is too long."}, status=400)

        compose_payload = {
            key: value for key, value in request.data.items()
            if key != "idempotency_key"
        }
        serializer = ComposeSerializer(data=compose_payload)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data["from_address"] = integration.mailbox.email

        if (
            data.get("signature_id")
            and not integration.has_permission("use_signatures")
        ):
            return Response(
                {"detail": "This connection cannot use signatures."}, status=403
            )

        mailbox = integration.mailbox
        sending.assert_organization_may_send(mailbox)
        recipients = sending.envelope_recipients(
            data.get("to") or [],
            data.get("cc") or [],
            data.get("bcc") or [],
        )
        if not recipients:
            return Response(
                {"detail": "Add at least one recipient."}, status=400
            )

        decision = ratelimit.hit(
            POSTBOX_SEND_PER_MAILBOX.bucket,
            str(mailbox.pk),
            limit=POSTBOX_SEND_PER_MAILBOX.limit,
            window=POSTBOX_SEND_PER_MAILBOX.window,
        )
        if not decision.allowed:
            raise Throttled(
                wait=decision.retry_after,
                detail="This mailbox is sending too quickly.",
            )

        message, identity = self.build(data, mailbox=mailbox)

        # Only reserve the idempotency key after all local validation succeeds.
        # Once reserved, a retry never sends again unless the first attempt
        # completed and recorded the original result.
        delivery, created = IntegrationDelivery.objects.get_or_create(
            integration=integration,
            idempotency_key=idempotency_key,
        )
        if not created:
            if delivery.status == "sent":
                return Response({
                    "sent": True,
                    "message_id": delivery.message_id,
                    "filed_in_sent": delivery.filed_in_sent,
                    "mailbox": integration.mailbox.email,
                    "idempotent_replay": True,
                })
            return Response(
                {"detail": "This message is already being processed. It will not be sent again."},
                status=409,
            )

        sending.submit(
            message,
            mailbox=mailbox,
            envelope_from=identity.address,
            recipients=recipients,
        )

        filed = False
        try:
            with imap.open_mailbox(mailbox.email) as connection:
                roles = {
                    folder.role: folder.name
                    for folder in connection.list_folders()
                    if folder.role
                }
                connection.append(
                    roles.get("sent", "Sent"),
                    message.as_bytes(),
                    flags="\\Seen",
                )
            filed = True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Integration message sent for mailbox %s but Sent filing "
                "failed: %r",
                mailbox.pk,
                exc,
            )

        delivery.status = "sent"
        delivery.message_id = message["Message-ID"] or ""
        delivery.filed_in_sent = filed
        delivery.completed_at = timezone.now()
        delivery.save(
            update_fields=["status", "message_id", "filed_in_sent", "completed_at"]
        )

        return Response(
            {
                "sent": True,
                "message_id": message["Message-ID"],
                "filed_in_sent": filed,
                "mailbox": mailbox.email,
            }
        )


class DisconnectView(APIView):
    authentication_classes = [IntegrationAccessAuthentication]
    permission_classes = []

    def post(self, request):
        token = request.integration_token
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
        return Response({"disconnected": True})
