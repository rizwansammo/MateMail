import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import Throttled
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.utils import (
    check_mailbox_limit,
    get_plan,
    reserve_resource_slot,
    resolve_mailbox_quota,
)
from apps.domains.models import Domain
from apps.domains.verification import DomainNotVerified, assert_provisionable
from apps.forward_groups.services import sync_all_groups_for_tenant
from apps.mail_directory.models import AccessGrantKind, MailboxAccessGrant
from apps.mail_directory.services import AddressConflict
from apps.mail_engine.errors import MailEngineError
from apps.mailboxes.models import Mailbox, MailboxKind
from apps.security import ratelimit
from apps.security.limits import MAILBOX_CREATE_PER_TENANT
from apps.tenants.permissions import (
    IsEmailVerified,
    IsTenantAdmin,
    TenantReadAdminWrite,
)
from apps.tenants.policy import MailNotPermitted, assert_can_use_mail

from .serializers import (
    TeamBoxCreateSerializer,
    TeamBoxMemberCreateSerializer,
    TeamBoxMemberSerializer,
    TeamBoxMemberUpdateSerializer,
    TeamBoxSerializer,
    TeamBoxStatusSerializer,
)
from .services import mark_sync_failure, sync_team_box

logger = logging.getLogger(__name__)


def _team_boxes(tenant):
    return (
        Mailbox.objects
        .for_tenant(tenant)
        .filter(kind=MailboxKind.TEAM_BOX)
        .select_related("domain")
    )


def _get_team_box(tenant, pk):
    return _team_boxes(tenant).filter(pk=pk).first()


class TeamBoxListCreateView(APIView):
    permission_classes = [
        IsAuthenticated,
        TenantReadAdminWrite,
        IsEmailVerified,
    ]

    def get(self, request):
        return Response(TeamBoxSerializer(_team_boxes(request.tenant), many=True).data)

    def post(self, request):
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
                    "Too many mail resources were created recently. "
                    "Please wait a little before creating another TeamBox."
                ),
            )

        try:
            assert_can_use_mail(request.tenant)
        except MailNotPermitted as exc:
            return Response({"detail": exc.customer_message}, status=403)

        serializer = TeamBoxCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        domain = (
            Domain.objects
            .for_tenant(request.tenant)
            .filter(pk=data["domain_id"])
            .first()
        )
        if not domain:
            return Response(
                {"domain_id": "Domain not found in this workspace."},
                status=400,
            )

        try:
            assert_provisionable(domain)
        except DomainNotVerified as exc:
            return Response({"domain_id": exc.customer_message}, status=409)

        plan = get_plan(request.tenant)
        quota_mb, quota_error = resolve_mailbox_quota(plan, data.get("quota_mb"))
        if quota_error:
            return Response({"quota_mb": quota_error}, status=400)

        with reserve_resource_slot(request.tenant, check_mailbox_limit) as slot:
            if not slot.allowed:
                return Response({"detail": slot.message}, status=402)
            try:
                team_box = Mailbox.objects.create(
                    tenant=request.tenant,
                    domain=domain,
                    local_part=data["local_part"],
                    full_name=data["display_name"],
                    quota_mb=quota_mb,
                    kind=MailboxKind.TEAM_BOX,
                )
            except AddressConflict as exc:
                return Response(
                    {"local_part": exc.customer_message},
                    status=400,
                )

        try:
            sync_team_box(team_box)
        except Exception as exc:
            mark_sync_failure(team_box, exc)

        from apps.logs.utils import log_event
        log_event(
            request.tenant,
            "teambox_created",
            request=request,
            team_box=team_box,
        )
        return Response(TeamBoxSerializer(team_box).data, status=201)


class TeamBoxDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def get(self, request, pk):
        team_box = _get_team_box(request.tenant, pk)
        if not team_box:
            return Response({"detail": "Not found."}, status=404)
        payload = TeamBoxSerializer(team_box).data
        grants = (
            team_box.access_grants_received
            .filter(grant_type=AccessGrantKind.TEAM_BOX, active=True)
            .select_related("grantee_mailbox")
        )
        payload["members"] = TeamBoxMemberSerializer(grants, many=True).data
        return Response(payload)

    def delete(self, request, pk):
        team_box = _get_team_box(request.tenant, pk)
        if not team_box:
            return Response({"detail": "Not found."}, status=404)

        for membership in team_box.forward_group_memberships.select_related("group"):
            if membership.group.members.count() <= 1:
                return Response(
                    {
                        "detail": (
                            f"{team_box.email} is the last member of Forward Group "
                            f"{membership.group.address}. Add another member or "
                            "delete the group first."
                        )
                    },
                    status=409,
                )

        if team_box.incoming_aliases.exists():
            return Response(
                {
                    "detail": (
                        "Remove aliases that point to this TeamBox before "
                        "deleting it."
                    )
                },
                status=409,
            )

        if team_box.mail_engine_provisioned:
            try:
                from apps.mail_engine.tasks import deprovision_mailbox_task
                deprovision_mailbox_task.delay(team_box.email)
            except Exception as exc:
                logger.error(
                    "Refusing to delete TeamBox %s because engine cleanup "
                    "could not be queued: %s",
                    team_box.email,
                    exc,
                )
                return Response(
                    {
                        "detail": (
                            "This TeamBox could not be removed right now. "
                            "Nothing has been changed — please try again shortly."
                        )
                    },
                    status=503,
                )

        # A deleted TeamBox cannot leave a browser with a session that silently
        # falls back to the personal mailbox on its next destructive request.
        # Revoke only sessions that were actively operating on this TeamBox;
        # other sessions for the same personal mailbox stay valid.
        from apps.postbox.models import PostBoxSession

        active_sessions = list(
            PostBoxSession.objects.filter(
                active_mailbox=team_box,
                revoked_at__isnull=True,
            )
        )
        for session in active_sessions:
            session.revoke()
        revoked_sessions = len(active_sessions)

        from apps.logs.utils import log_event
        log_event(
            request.tenant,
            "teambox_deleted",
            request=request,
            team_box=team_box,
            metadata={"active_postbox_sessions_revoked": revoked_sessions},
        )
        team_box.delete()
        sync_all_groups_for_tenant(request.tenant)
        return Response(status=204)


class TeamBoxStatusView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def patch(self, request, pk):
        team_box = _get_team_box(request.tenant, pk)
        if not team_box:
            return Response({"detail": "Not found."}, status=404)

        serializer = TeamBoxStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        if team_box.status == new_status:
            return Response(TeamBoxSerializer(team_box).data)

        if new_status == "active":
            try:
                assert_can_use_mail(request.tenant)
                assert_provisionable(team_box.domain)
            except MailNotPermitted as exc:
                return Response({"detail": exc.customer_message}, status=403)
            except DomainNotVerified as exc:
                return Response({"detail": exc.customer_message}, status=409)

        if team_box.mail_engine_provisioned:
            try:
                from apps.mail_engine.factory import get_adapter
                get_adapter().set_mailbox_active(
                    team_box.email,
                    active=new_status == "active",
                )
            except MailEngineError as exc:
                return Response({"detail": exc.customer_message}, status=503)

        team_box.status = new_status
        team_box.save(update_fields=["status", "updated_at"])
        sync_all_groups_for_tenant(request.tenant)
        return Response(TeamBoxSerializer(team_box).data)


class TeamBoxReprovisionView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin, IsEmailVerified]

    def post(self, request, pk):
        team_box = _get_team_box(request.tenant, pk)
        if not team_box:
            return Response({"detail": "Not found."}, status=404)

        try:
            assert_can_use_mail(request.tenant)
            assert_provisionable(team_box.domain)
        except MailNotPermitted as exc:
            return Response({"detail": exc.customer_message}, status=403)
        except DomainNotVerified as exc:
            return Response({"detail": exc.customer_message}, status=409)

        try:
            sync_team_box(team_box)
        except MailEngineError as exc:
            mark_sync_failure(team_box, exc)
            return Response({"detail": exc.customer_message}, status=503)
        except Exception as exc:
            mark_sync_failure(team_box, exc)
            return Response(
                {"detail": MailEngineError.customer_message},
                status=503,
            )

        return Response(TeamBoxSerializer(team_box).data)


class TeamBoxMemberListCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def get(self, request, pk):
        team_box = _get_team_box(request.tenant, pk)
        if not team_box:
            return Response({"detail": "Not found."}, status=404)
        grants = (
            team_box.access_grants_received
            .filter(grant_type=AccessGrantKind.TEAM_BOX, active=True)
            .select_related("grantee_mailbox")
        )
        return Response(TeamBoxMemberSerializer(grants, many=True).data)

    def post(self, request, pk):
        team_box = _get_team_box(request.tenant, pk)
        if not team_box:
            return Response({"detail": "Not found."}, status=404)

        serializer = TeamBoxMemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        member = (
            Mailbox.objects
            .for_tenant(request.tenant)
            .filter(pk=data["mailbox_id"], kind=MailboxKind.PERSONAL)
            .first()
        )
        if not member:
            return Response(
                {"mailbox_id": "Personal mailbox not found in this workspace."},
                status=400,
            )

        if MailboxAccessGrant.objects.filter(
            target_mailbox=team_box,
            grantee_mailbox=member,
        ).exists():
            return Response(
                {"mailbox_id": "This mailbox is already a TeamBox member."},
                status=400,
            )

        grant = MailboxAccessGrant(
            tenant=request.tenant,
            target_mailbox=team_box,
            grantee_mailbox=member,
            grant_type=AccessGrantKind.TEAM_BOX,
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

        try:
            sync_team_box(team_box)
        except Exception as exc:
            grant.delete()
            mark_sync_failure(team_box, exc)
            return Response(
                {"detail": MailEngineError.customer_message},
                status=503,
            )

        from apps.logs.utils import log_event
        log_event(
            request.tenant,
            "teambox_member_added",
            request=request,
            team_box=team_box,
            member=member,
        )
        return Response(TeamBoxMemberSerializer(grant).data, status=201)


class TeamBoxMemberDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def _get(self, request, pk, member_pk):
        team_box = _get_team_box(request.tenant, pk)
        if not team_box:
            return None, None
        grant = (
            MailboxAccessGrant.objects
            .filter(
                pk=member_pk,
                tenant=request.tenant,
                target_mailbox=team_box,
                grant_type=AccessGrantKind.TEAM_BOX,
                active=True,
            )
            .select_related("grantee_mailbox")
            .first()
        )
        return team_box, grant

    def patch(self, request, pk, member_pk):
        team_box, grant = self._get(request, pk, member_pk)
        if not team_box or not grant:
            return Response({"detail": "Not found."}, status=404)

        serializer = TeamBoxMemberUpdateSerializer(data=request.data)
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
        }
        if proposed["can_manage"] and not proposed["can_read"]:
            return Response(
                {"can_manage": "Manage permission requires read permission."},
                status=400,
            )
        if not any(proposed.values()):
            return Response(
                {"detail": "A TeamBox member must have at least one permission."},
                status=400,
            )

        before = {
            "can_read": grant.can_read,
            "can_manage": grant.can_manage,
            "can_send_as": grant.can_send_as,
            "can_send_on_behalf": grant.can_send_on_behalf,
        }
        for field, value in proposed.items():
            setattr(grant, field, value)

        try:
            grant.save()
            sync_team_box(team_box)
        except Exception as exc:
            for field, value in before.items():
                setattr(grant, field, value)
            grant.save()
            mark_sync_failure(team_box, exc)
            return Response(
                {"detail": MailEngineError.customer_message},
                status=503,
            )

        return Response(TeamBoxMemberSerializer(grant).data)

    def delete(self, request, pk, member_pk):
        team_box, grant = self._get(request, pk, member_pk)
        if not team_box or not grant:
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
        member = grant.grantee_mailbox
        grant.delete()

        try:
            sync_team_box(team_box)
        except Exception as exc:
            MailboxAccessGrant.objects.create(**snapshot)
            mark_sync_failure(team_box, exc)
            return Response(
                {"detail": MailEngineError.customer_message},
                status=503,
            )

        from apps.logs.utils import log_event
        log_event(
            request.tenant,
            "teambox_member_removed",
            request=request,
            team_box=team_box,
            member=member,
        )
        return Response(status=204)
