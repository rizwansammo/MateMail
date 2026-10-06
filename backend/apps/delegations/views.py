import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.logs.utils import log_event
from apps.mail_directory.models import AccessGrantKind, MailboxAccessGrant
from apps.mail_engine.errors import MailEngineError
from apps.mailboxes.models import Mailbox, MailboxKind, MailboxStatus
from apps.tenants.permissions import IsEmailVerified, IsTenantAdmin

from .serializers import (
    DelegationCreateSerializer,
    DelegationSerializer,
    DelegationUpdateSerializer,
)
from .services import grant_has_sender_right, sync_delegated_mailbox

logger = logging.getLogger(__name__)


def _delegations(tenant):
    return (
        MailboxAccessGrant.objects
        .filter(
            tenant=tenant,
            grant_type=AccessGrantKind.DELEGATION,
        )
        .select_related(
            "target_mailbox",
            "target_mailbox__domain",
            "grantee_mailbox",
            "grantee_mailbox__domain",
        )
        .order_by("target_mailbox__email", "grantee_mailbox__email")
    )


def _get_delegation(tenant, pk):
    return _delegations(tenant).filter(pk=pk).first()


def _active_personal_mailbox(tenant, pk):
    return (
        Mailbox.objects
        .for_tenant(tenant)
        .filter(
            pk=pk,
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        .select_related("tenant", "domain")
        .first()
    )


def _sync_or_response(mailbox):
    try:
        sync_delegated_mailbox(mailbox)
    except MailEngineError as exc:
        logger.error(
            "Delegation sender authorization sync failed for %s: %s",
            mailbox.email,
            exc.log_message,
        )
        return Response({"detail": exc.customer_message}, status=503)
    except Exception:
        logger.exception(
            "Unexpected Delegation sender authorization failure for %s",
            mailbox.email,
        )
        return Response({"detail": MailEngineError.customer_message}, status=503)
    return None


class DelegationListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin, IsEmailVerified]

    def get(self, request):
        return Response(
            DelegationSerializer(_delegations(request.tenant), many=True).data
        )

    def post(self, request):
        serializer = DelegationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        target = _active_personal_mailbox(
            request.tenant,
            data["target_mailbox_id"],
        )
        if not target:
            return Response(
                {
                    "target_mailbox_id": (
                        "Choose an active, provisioned personal mailbox in this organization."
                    )
                },
                status=400,
            )

        delegate = _active_personal_mailbox(
            request.tenant,
            data["delegate_mailbox_id"],
        )
        if not delegate:
            return Response(
                {
                    "delegate_mailbox_id": (
                        "Choose an active, provisioned personal mailbox in this organization."
                    )
                },
                status=400,
            )

        if MailboxAccessGrant.objects.filter(
            target_mailbox=target,
            grantee_mailbox=delegate,
        ).exists():
            return Response(
                {
                    "delegate_mailbox_id": (
                        "This mailbox already has access to the target mailbox."
                    )
                },
                status=400,
            )

        grant = MailboxAccessGrant(
            tenant=request.tenant,
            target_mailbox=target,
            grantee_mailbox=delegate,
            grant_type=AccessGrantKind.DELEGATION,
            can_read=data["can_read"],
            can_manage=data["can_manage"],
            can_send_as=data["can_send_as"],
            can_send_on_behalf=data["can_send_on_behalf"],
            active=True,
        )
        try:
            grant.save()
        except DjangoValidationError as exc:
            return Response({"detail": exc.message_dict}, status=400)

        if grant_has_sender_right(grant):
            error = _sync_or_response(target)
            if error:
                grant.delete()
                return error

        log_event(
            request.tenant,
            "mailbox_delegation_created",
            request=request,
            source=delegate.email,
            metadata={
                "target_mailbox": target.email,
                "can_read": grant.can_read,
                "can_manage": grant.can_manage,
                "can_send_as": grant.can_send_as,
                "can_send_on_behalf": grant.can_send_on_behalf,
            },
        )
        return Response(DelegationSerializer(grant).data, status=201)


class DelegationDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin, IsEmailVerified]

    def patch(self, request, pk):
        grant = _get_delegation(request.tenant, pk)
        if not grant:
            return Response({"detail": "Not found."}, status=404)

        serializer = DelegationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        proposed = {
            "can_read": data.get("can_read", grant.can_read),
            "can_manage": data.get("can_manage", grant.can_manage),
            "can_send_as": data.get("can_send_as", grant.can_send_as),
            "can_send_on_behalf": data.get(
                "can_send_on_behalf",
                grant.can_send_on_behalf,
            ),
            "active": data.get("active", grant.active),
        }

        if proposed["can_manage"] and not proposed["can_read"]:
            return Response(
                {"can_manage": "Manage permission requires read permission."},
                status=400,
            )
        if proposed["active"] and not any(
            proposed[field]
            for field in (
                "can_read",
                "can_manage",
                "can_send_as",
                "can_send_on_behalf",
            )
        ):
            return Response(
                {"active": "An active delegation must grant at least one permission."},
                status=400,
            )

        before = {
            "can_read": grant.can_read,
            "can_manage": grant.can_manage,
            "can_send_as": grant.can_send_as,
            "can_send_on_behalf": grant.can_send_on_behalf,
            "active": grant.active,
        }
        sender_before = (
            before["active"]
            and (before["can_send_as"] or before["can_send_on_behalf"])
        )

        for field, value in proposed.items():
            setattr(grant, field, value)

        try:
            grant.save()
        except DjangoValidationError as exc:
            return Response({"detail": exc.message_dict}, status=400)

        sender_after = (
            grant.active
            and (grant.can_send_as or grant.can_send_on_behalf)
        )
        if sender_before != sender_after:
            error = _sync_or_response(grant.target_mailbox)
            if error:
                for field, value in before.items():
                    setattr(grant, field, value)
                grant.save()
                return error

        log_event(
            request.tenant,
            "mailbox_delegation_updated",
            request=request,
            source=grant.grantee_mailbox.email,
            metadata={
                "target_mailbox": grant.target_mailbox.email,
                **proposed,
            },
        )
        return Response(DelegationSerializer(grant).data)

    def delete(self, request, pk):
        grant = _get_delegation(request.tenant, pk)
        if not grant:
            return Response({"detail": "Not found."}, status=404)

        snapshot = {
            "id": grant.id,
            "tenant": grant.tenant,
            "target_mailbox": grant.target_mailbox,
            "grantee_mailbox": grant.grantee_mailbox,
            "grant_type": grant.grant_type,
            "can_read": grant.can_read,
            "can_manage": grant.can_manage,
            "can_send_as": grant.can_send_as,
            "can_send_on_behalf": grant.can_send_on_behalf,
            "active": grant.active,
        }
        target = grant.target_mailbox
        delegate = grant.grantee_mailbox
        had_sender_right = grant.active and grant_has_sender_right(grant)
        grant.delete()

        if had_sender_right:
            error = _sync_or_response(target)
            if error:
                MailboxAccessGrant.objects.create(**snapshot)
                return error

        log_event(
            request.tenant,
            "mailbox_delegation_deleted",
            request=request,
            source=delegate.email,
            metadata={"target_mailbox": target.email},
        )
        return Response(status=204)
