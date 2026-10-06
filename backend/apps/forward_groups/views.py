import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.models import Domain
from apps.domains.verification import DomainNotVerified, assert_provisionable
from apps.logs.utils import log_event
from apps.mail_directory.services import AddressConflict
from apps.mail_engine.errors import MailEngineError
from apps.mail_engine.factory import get_adapter
from apps.mailboxes.models import Mailbox, MailboxKind, MailboxStatus
from apps.tenants.permissions import IsEmailVerified, IsTenantAdmin, TenantReadAdminWrite
from apps.tenants.policy import MailNotPermitted, assert_can_use_mail

from .models import (
    ForwardGroup,
    ForwardGroupAllowedSender,
    ForwardGroupMember,
    ForwardGroupMemberRole,
    ForwardGroupSenderPolicy,
    ForwardGroupStatus,
)
from .serializers import (
    ForwardGroupAllowedSenderSerializer,
    ForwardGroupCreateSerializer,
    ForwardGroupMemberCreateSerializer,
    ForwardGroupMemberSerializer,
    ForwardGroupMemberUpdateSerializer,
    ForwardGroupPolicySerializer,
    ForwardGroupSenderCreateSerializer,
    ForwardGroupSerializer,
    ForwardGroupStatusSerializer,
)
from .services import (
    ForwardGroupLoop,
    assert_member_will_not_loop,
    mark_sync_failure,
    resolved_destinations,
    sync_group,
)

logger = logging.getLogger(__name__)


def _groups(tenant):
    return (
        ForwardGroup.objects
        .for_tenant(tenant)
        .select_related("domain")
        .prefetch_related(
            "members__mailbox",
            "allowed_senders__mailbox",
        )
    )


def _get_group(tenant, pk):
    return _groups(tenant).filter(pk=pk).first()


def _detail_payload(group):
    payload = ForwardGroupSerializer(group).data
    payload["members"] = ForwardGroupMemberSerializer(
        group.members.select_related("mailbox"),
        many=True,
    ).data
    payload["allowed_senders"] = ForwardGroupAllowedSenderSerializer(
        group.allowed_senders.select_related("mailbox"),
        many=True,
    ).data
    return payload


def _sync_or_error(group):
    try:
        sync_group(group)
    except MailEngineError as exc:
        mark_sync_failure(group, exc)
        return Response({"detail": exc.customer_message}, status=503)
    except Exception as exc:  # noqa: BLE001
        mark_sync_failure(group, exc)
        return Response({"detail": MailEngineError.customer_message}, status=503)
    return None


class ForwardGroupListCreateView(APIView):
    permission_classes = [
        IsAuthenticated,
        TenantReadAdminWrite,
        IsEmailVerified,
    ]

    def get(self, request):
        return Response(ForwardGroupSerializer(_groups(request.tenant), many=True).data)

    def post(self, request):
        try:
            assert_can_use_mail(request.tenant)
        except MailNotPermitted as exc:
            return Response({"detail": exc.customer_message}, status=403)

        serializer = ForwardGroupCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        domain = (
            Domain.objects
            .for_tenant(request.tenant)
            .filter(pk=data["domain_id"])
            .first()
        )
        if not domain:
            return Response({"domain_id": "Domain not found in this workspace."}, status=400)

        try:
            assert_provisionable(domain)
        except DomainNotVerified as exc:
            return Response({"domain_id": exc.customer_message}, status=409)

        member_ids = data["member_mailbox_ids"]
        members = list(
            Mailbox.objects
            .for_tenant(request.tenant)
            .filter(pk__in=member_ids)
            .select_related("domain")
        )
        if len(members) != len(member_ids):
            return Response(
                {"member_mailbox_ids": "One or more member mailboxes were not found."},
                status=400,
            )

        sender_ids = data.get("allowed_sender_mailbox_ids") or []
        senders = list(
            Mailbox.objects
            .for_tenant(request.tenant)
            .filter(
                pk__in=sender_ids,
                kind=MailboxKind.PERSONAL,
            )
        )
        if len(senders) != len(sender_ids):
            return Response(
                {
                    "allowed_sender_mailbox_ids": (
                        "Selected senders must be personal mailboxes in this organization."
                    )
                },
                status=400,
            )

        try:
            with transaction.atomic():
                group = ForwardGroup.objects.create(
                    tenant=request.tenant,
                    domain=domain,
                    local_part=data["local_part"],
                    display_name=data["display_name"],
                    sender_policy=data["sender_policy"],
                )
                for mailbox in members:
                    assert_member_will_not_loop(group, mailbox)
                    ForwardGroupMember.objects.create(
                        group=group,
                        mailbox=mailbox,
                        role=ForwardGroupMemberRole.MEMBER,
                    )
                for mailbox in senders:
                    ForwardGroupAllowedSender.objects.create(
                        group=group,
                        mailbox=mailbox,
                    )
        except AddressConflict as exc:
            return Response({"local_part": exc.customer_message}, status=400)
        except ForwardGroupLoop as exc:
            return Response({"member_mailbox_ids": exc.customer_message}, status=400)
        except DjangoValidationError as exc:
            return Response({"detail": exc.message_dict}, status=400)

        try:
            sync_group(group)
        except Exception as exc:  # noqa: BLE001
            mark_sync_failure(group, exc)
            payload = _detail_payload(group)
            payload["detail"] = MailEngineError.customer_message
            return Response(payload, status=202)

        log_event(
            request.tenant,
            "forward_group_created",
            request=request,
            source=group.address,
            metadata={
                "members": len(members),
                "sender_policy": group.sender_policy,
            },
        )
        return Response(_detail_payload(group), status=201)


class ForwardGroupDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def get(self, request, pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)
        return Response(_detail_payload(group))

    def delete(self, request, pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)

        try:
            get_adapter().delete_forward_group(group.address)
        except MailEngineError as exc:
            return Response({"detail": exc.customer_message}, status=503)

        address = group.address
        group.delete()
        log_event(
            request.tenant,
            "forward_group_deleted",
            request=request,
            source=address,
        )
        return Response(status=204)


class ForwardGroupStatusView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def patch(self, request, pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)

        serializer = ForwardGroupStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        if group.status == new_status:
            return Response(_detail_payload(group))

        if new_status == ForwardGroupStatus.ACTIVE:
            try:
                assert_can_use_mail(request.tenant)
                assert_provisionable(group.domain)
            except MailNotPermitted as exc:
                return Response({"detail": exc.customer_message}, status=403)
            except DomainNotVerified as exc:
                return Response({"detail": exc.customer_message}, status=409)

            if not resolved_destinations(group):
                return Response(
                    {"detail": "A Forward Group needs at least one active member."},
                    status=400,
                )
            for membership in group.members.select_related("mailbox"):
                try:
                    assert_member_will_not_loop(group, membership.mailbox)
                except ForwardGroupLoop as exc:
                    return Response({"detail": exc.customer_message}, status=400)

        previous = group.status
        group.status = new_status
        group.save(update_fields=["status", "updated_at"])

        error = _sync_or_error(group)
        if error:
            group.status = previous
            group.save(update_fields=["status", "updated_at"])
            return error

        return Response(_detail_payload(group))


class ForwardGroupPolicyView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def patch(self, request, pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)

        serializer = ForwardGroupPolicySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        previous = group.sender_policy
        group.sender_policy = serializer.validated_data["sender_policy"]
        group.save(update_fields=["sender_policy", "updated_at"])

        error = _sync_or_error(group)
        if error:
            group.sender_policy = previous
            group.save(update_fields=["sender_policy", "updated_at"])
            return error

        return Response(_detail_payload(group))


class ForwardGroupReprovisionView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin, IsEmailVerified]

    def post(self, request, pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)

        try:
            assert_can_use_mail(request.tenant)
            assert_provisionable(group.domain)
        except MailNotPermitted as exc:
            return Response({"detail": exc.customer_message}, status=403)
        except DomainNotVerified as exc:
            return Response({"detail": exc.customer_message}, status=409)

        error = _sync_or_error(group)
        if error:
            return error
        return Response(_detail_payload(group))


class ForwardGroupMemberListCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def get(self, request, pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)
        return Response(
            ForwardGroupMemberSerializer(
                group.members.select_related("mailbox"),
                many=True,
            ).data
        )

    def post(self, request, pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)

        serializer = ForwardGroupMemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        mailbox = (
            Mailbox.objects
            .for_tenant(request.tenant)
            .filter(pk=data["mailbox_id"])
            .first()
        )
        if not mailbox:
            return Response({"mailbox_id": "Mailbox not found."}, status=400)

        if group.members.filter(mailbox=mailbox).exists():
            return Response({"mailbox_id": "That mailbox is already a member."}, status=400)

        try:
            assert_member_will_not_loop(group, mailbox)
        except ForwardGroupLoop as exc:
            return Response({"mailbox_id": exc.customer_message}, status=400)

        member = ForwardGroupMember.objects.create(
            group=group,
            mailbox=mailbox,
            role=data["role"],
        )

        error = _sync_or_error(group)
        if error:
            member.delete()
            return error

        return Response(ForwardGroupMemberSerializer(member).data, status=201)


class ForwardGroupMemberDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def _get(self, request, pk, member_pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return None, None
        member = (
            group.members
            .select_related("mailbox")
            .filter(pk=member_pk)
            .first()
        )
        return group, member

    def patch(self, request, pk, member_pk):
        group, member = self._get(request, pk, member_pk)
        if not group or not member:
            return Response({"detail": "Not found."}, status=404)

        serializer = ForwardGroupMemberUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member.role = serializer.validated_data["role"]
        member.save(update_fields=["role"])
        return Response(ForwardGroupMemberSerializer(member).data)

    def delete(self, request, pk, member_pk):
        group, member = self._get(request, pk, member_pk)
        if not group or not member:
            return Response({"detail": "Not found."}, status=404)

        if group.members.count() <= 1:
            return Response(
                {"detail": "A Forward Group must keep at least one member."},
                status=400,
            )

        snapshot = {
            "group": group,
            "mailbox": member.mailbox,
            "role": member.role,
        }
        member.delete()

        error = _sync_or_error(group)
        if error:
            ForwardGroupMember.objects.create(**snapshot)
            return error
        return Response(status=204)


class ForwardGroupSenderListCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def get(self, request, pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)
        return Response(
            ForwardGroupAllowedSenderSerializer(
                group.allowed_senders.select_related("mailbox"),
                many=True,
            ).data
        )

    def post(self, request, pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)

        serializer = ForwardGroupSenderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        mailbox = (
            Mailbox.objects
            .for_tenant(request.tenant)
            .filter(
                pk=serializer.validated_data["mailbox_id"],
                kind=MailboxKind.PERSONAL,
            )
            .first()
        )
        if not mailbox:
            return Response(
                {"mailbox_id": "Personal mailbox not found in this organization."},
                status=400,
            )

        if group.allowed_senders.filter(mailbox=mailbox).exists():
            return Response(
                {"mailbox_id": "That mailbox is already an allowed sender."},
                status=400,
            )

        sender = ForwardGroupAllowedSender.objects.create(
            group=group,
            mailbox=mailbox,
        )
        error = _sync_or_error(group)
        if error:
            sender.delete()
            return error
        return Response(
            ForwardGroupAllowedSenderSerializer(sender).data,
            status=201,
        )


class ForwardGroupSenderDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def delete(self, request, pk, sender_pk):
        group = _get_group(request.tenant, pk)
        if not group:
            return Response({"detail": "Not found."}, status=404)

        sender = (
            group.allowed_senders
            .select_related("mailbox")
            .filter(pk=sender_pk)
            .first()
        )
        if not sender:
            return Response({"detail": "Not found."}, status=404)

        mailbox = sender.mailbox
        sender.delete()
        error = _sync_or_error(group)
        if error:
            ForwardGroupAllowedSender.objects.create(
                group=group,
                mailbox=mailbox,
            )
            return error
        return Response(status=204)
