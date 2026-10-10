import hashlib

from django.conf import settings
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db import transaction

from apps.accounts.mailer import send_transactional
from apps.accounts.serializers import UserProfileSerializer
from apps.accounts.tokens import make_tokens
from apps.accounts.views import authenticated_response
from apps.billing.utils import check_member_limit
from apps.logs.models import LogEventType
from apps.logs.utils import log_event
from apps.security.scopes import normalise as normalise_scopes
from apps.tenants.models import MemberRole, MemberStatus, Tenant, TenantMembership
from apps.tenants.membership_policy import (
    MEMBER_DOMAIN_ERROR,
    SINGLE_ORGANIZATION_ERROR,
    tenant_allows_member_email,
    user_has_other_active_tenant,
)
from apps.tenants.permissions import HasTenantAccess, IsTenantAdmin
from .invite_mailbox import (
    InviteMailboxError, complete_invited_mailbox,
    preflight_invited_mailbox, reserve_invited_mailbox,
)
from .models import APIKey, TeamInvite
from .serializers import (
    APIKeyCreateSerializer,
    APIKeySerializer,
    APIKeyUpdateSerializer,
    TeamInviteCreateSerializer,
    TeamInviteSerializer,
)


class TeamInviteListView(APIView):
    permission_classes = [IsTenantAdmin]

    def get(self, request):
        invites = TeamInvite.objects.filter(
            tenant=request.tenant,
            is_revoked=False,
            accepted_at__isnull=True,
        )
        return Response(TeamInviteSerializer(invites, many=True).data)

    def post(self, request):
        serializer = TeamInviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()
        role = serializer.validated_data["role"]
        create_mailbox = serializer.validated_data["create_mailbox"]

        if not tenant_allows_member_email(request.tenant, email):
            return Response({"detail": MEMBER_DOMAIN_ERROR}, status=400)

        # Reject if a pending invite already exists for this email
        existing = TeamInvite.objects.filter(
            tenant=request.tenant,
            email=email,
            is_revoked=False,
            accepted_at__isnull=True,
        ).first()
        if existing and existing.is_pending:
            return Response(
                {"detail": "A pending invite already exists for that email."},
                status=400,
            )

        # Reject if already an active member
        from apps.accounts.models import User
        existing_user = User.objects.filter(email=email).first()
        if existing_user:
            already_member = TenantMembership.objects.filter(
                tenant=request.tenant, user=existing_user, status=MemberStatus.ACTIVE
            ).exists()
            if already_member:
                return Response(
                    {"detail": "That person is already an active member of this organization."},
                    status=400,
                )
            if user_has_other_active_tenant(existing_user, request.tenant):
                return Response({"detail": SINGLE_ORGANIZATION_ERROR}, status=400)

        # A pending invite holds a seat (see billing.utils.count_member_slots).
        # Checked and taken under a lock on the tenant row, so two admins
        # inviting at the same moment cannot both pass the same count.
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().get(pk=request.tenant.pk)
            allowed, msg = check_member_limit(tenant)
            if not allowed:
                return Response({"detail": msg}, status=403)
            # Close the duplicate-invite race under the same tenant lock.
            if TeamInvite.objects.filter(
                tenant=tenant, email=email, is_revoked=False,
                accepted_at__isnull=True, expires_at__gt=timezone.now(),
            ).exists():
                return Response({"detail": "A pending invite already exists for that email."}, status=400)
            if create_mailbox:
                try:
                    preflight_invited_mailbox(tenant, email)
                except InviteMailboxError as exc:
                    return Response({"detail": exc.message}, status=exc.status_code)
            raw, invite = TeamInvite.make(
                tenant, email, role, request.user, create_mailbox=create_mailbox
            )

        accept_url = f"{settings.FRONTEND_URL}/accept-invite?token={raw}"

        role_display = role.replace("_", " ")
        inviter = request.user.full_name or request.user.email
        delivered = send_transactional(
            subject=f"You've been invited to {request.tenant.name} on MateMail",
            body=(
                f"Hi,\n\n"
                f"{inviter} has invited you to join {request.tenant.name} "
                f"on MateMail Hub as {role_display}.\n\n"
                f"Accept your invitation (expires in 7 days):\n{accept_url}\n\n"
                f"If you don't have a MateMail account yet, the invite link will create "
                f"your account directly inside this organization.\n\n"
                + (
                    "A personal mailbox has been requested for your email. "
                    "Choose its password when you accept this invitation.\n\n"
                    if create_mailbox else ""
                )
                + "— MateMail, by NetaMate Solutions"
            ),
            to=email,
            purpose="team-invite",
        )

        # The invite row is real whether or not the mail went out, so it is
        # returned either way — revoking a half-created invite would be worse.
        # But the admin is told, because otherwise they sit waiting for someone
        # who was never contacted.
        data = TeamInviteSerializer(invite).data
        # One-time secret link, only in this owner/admin POST response, never list/preview.
        data["invite_url"] = accept_url
        data["email_delivered"] = delivered
        if not delivered:
            data["detail"] = (
                "The invitation was created but the email could not be sent. "
                "Share the invite link directly, or try resending later."
            )
        return Response(data, status=201)


class TeamInviteRevokeView(APIView):
    permission_classes = [IsTenantAdmin]

    def delete(self, request, invite_id):
        try:
            invite = TeamInvite.objects.get(
                id=invite_id, tenant=request.tenant, is_revoked=False
            )
        except TeamInvite.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        invite.is_revoked = True
        invite.save(update_fields=["is_revoked"])
        return Response(status=204)


class TeamInviteAcceptView(APIView):
    """Validates an invite token and adds the authenticated user to the workspace."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("token", "").strip()
        if not token:
            return Response({"detail": "Token is required."}, status=400)

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        try:
            invite = TeamInvite.objects.select_related("tenant").get(
                token_hash=token_hash,
                is_revoked=False,
                accepted_at__isnull=True,
            )
        except TeamInvite.DoesNotExist:
            return Response({"detail": "Invalid or expired invite link."}, status=400)

        if not invite.is_pending:
            return Response({"detail": "This invite has expired."}, status=400)

        if request.user.email.lower() != invite.email.lower():
            return Response(
                {
                    "detail": (
                        f"This invite was sent to {invite.email}. "
                        f"Please log in with that email address to accept it."
                    )
                },
                status=403,
            )

        if not tenant_allows_member_email(invite.tenant, request.user.email):
            return Response({"detail": MEMBER_DOMAIN_ERROR}, status=403)

        if user_has_other_active_tenant(request.user, invite.tenant):
            return Response({"detail": SINGLE_ORGANIZATION_ERROR}, status=403)

        mailbox_password = request.data.get("mailbox_password", "")
        if invite.create_mailbox and (
            not isinstance(mailbox_password, str) or len(mailbox_password) < 10
        ):
            return Response(
                {"detail": "Choose a mailbox password of at least 10 characters."}, status=400
            )

        mailbox = None
        try:
            with transaction.atomic():
                tenant = Tenant.objects.select_for_update().get(pk=invite.tenant_id)
                invite = TeamInvite.objects.select_for_update().get(pk=invite.pk)
                if not invite.is_pending:
                    return Response({"detail": "This invitation is no longer valid."}, status=400)
    
                existing = TenantMembership.objects.filter(
                    tenant=invite.tenant, user=request.user
                ).first()
                if existing and existing.status == MemberStatus.ACTIVE:
                    invite.accepted_at = timezone.now()
                    invite.save(update_fields=["accepted_at"])
                    tokens = make_tokens(request.user, tenant_id=invite.tenant_id)
                    return authenticated_response(
                        tokens,
                        {
                            "detail": "Already a member.",
                            "user": UserProfileSerializer(request.user).data,
                            "tenant": {
                                "id": str(invite.tenant.id),
                                "name": invite.tenant.name,
                                "slug": invite.tenant.slug,
                                "status": invite.tenant.status,
                                "role": existing.role,
                            },
                        },
                        status=200,
                    )
    
                # This invite already occupies a seat, so the post-acceptance total
                # is unchanged and `additional=0` is the right question to ask. The
                # check is still needed: the plan can be downgraded between the
                # invitation being sent and the recipient clicking the link.
                allowed, msg = check_member_limit(tenant, additional=0)
                if not allowed:
                    return Response({"detail": msg}, status=403)
    
                # Reserve address and seat under the SAME database transaction.
                mailbox = reserve_invited_mailbox(tenant, invite, request.user.full_name)
                if existing:
                    existing.role = invite.role
                    existing.status = MemberStatus.ACTIVE
                    existing.save(update_fields=["role", "status", "updated_at"])
                else:
                    TenantMembership.objects.create(
                        tenant=invite.tenant,
                        user=request.user,
                        role=invite.role,
                        status=MemberStatus.ACTIVE,
                        invited_by_id=invite.invited_by_id,
                    )
    
                invite.accepted_at = timezone.now()
                invite.save(update_fields=["accepted_at"])
        except InviteMailboxError as exc:
            return Response({"detail": exc.message}, status=exc.status_code)

        mailbox_result = (
            complete_invited_mailbox(mailbox, mailbox_password, request=request)
            if mailbox is not None else None
        )

        membership = TenantMembership.objects.get(
            tenant=invite.tenant,
            user=request.user,
            status=MemberStatus.ACTIVE,
        )
        tokens = make_tokens(request.user, tenant_id=invite.tenant_id)
        return authenticated_response(
            tokens,
            {
                "detail": "Invite accepted.",
                "mailbox": mailbox_result,
                "user": UserProfileSerializer(request.user).data,
                "tenant": {
                    "id": str(invite.tenant.id),
                    "name": invite.tenant.name,
                    "slug": invite.tenant.slug,
                    "status": invite.tenant.status,
                    "role": membership.role,
                },
            },
            status=200,
        )


class TeamInvitePreviewView(APIView):
    """Public endpoint — returns invite metadata without requiring auth (for the accept page)."""
    permission_classes = []

    def get(self, request):
        token = request.query_params.get("token", "").strip()
        if not token:
            return Response({"detail": "Token is required."}, status=400)

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        try:
            invite = TeamInvite.objects.select_related("tenant", "invited_by").get(
                token_hash=token_hash,
                is_revoked=False,
                accepted_at__isnull=True,
            )
        except TeamInvite.DoesNotExist:
            return Response({"detail": "Invalid or expired invite link.", "valid": False}, status=200)

        return Response(
            {
                "valid": invite.is_pending and tenant_allows_member_email(invite.tenant, invite.email),
                "email": invite.email,
                "role": invite.role,
                "tenant_name": invite.tenant.name,
                "invited_by": invite.invited_by.full_name or invite.invited_by.email if invite.invited_by else None,
                "expires_at": invite.expires_at,
                "create_mailbox": invite.create_mailbox,
            }
        )


class APIKeyListView(APIView):
    permission_classes = [IsTenantAdmin]

    def get(self, request):
        keys = APIKey.objects.filter(tenant=request.tenant)
        return Response(APIKeySerializer(keys, many=True).data)

    def post(self, request):
        serializer = APIKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"]
        expires_at = serializer.validated_data.get("expires_at")
        scopes = serializer.validated_data.get("scopes")

        raw, api_key = APIKey.make(
            request.tenant, name, request.user, expires_at, scopes=scopes
        )

        log_event(
            request.tenant,
            LogEventType.API_KEY_CREATED,
            request=request,
            metadata={
                "name": api_key.name,
                "key_prefix": api_key.key_prefix,
                "scopes": api_key.scopes,
            },
        )

        data = APIKeySerializer(api_key).data
        data["key"] = raw  # Shown ONCE at creation — not stored
        return Response(data, status=201)


class APIKeyDetailView(APIView):
    """
    PATCH /api/teams/apikeys/{id}/ — rename a key or change its scopes.

    Separate from creation because widening a key's scopes is a privilege
    change on a credential that already exists somewhere outside MateMail, and
    it is audited as one.
    """

    permission_classes = [IsTenantAdmin]

    def patch(self, request, key_id):
        try:
            key = APIKey.objects.get(id=key_id, tenant=request.tenant)
        except APIKey.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        serializer = APIKeyUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        updated = []
        if "name" in data:
            key.name = data["name"]
            updated.append("name")

        previous = normalise_scopes(key.scopes)
        if "scopes" in data and data["scopes"] != previous:
            key.scopes = data["scopes"]
            updated.append("scopes")

        if updated:
            key.save(update_fields=updated)

        if "scopes" in updated:
            log_event(
                request.tenant,
                LogEventType.API_KEY_SCOPES_CHANGED,
                request=request,
                metadata={
                    "key_prefix": key.key_prefix,
                    "from": previous,
                    "to": key.scopes,
                },
            )

        return Response(APIKeySerializer(key).data)


class APIKeyRevokeView(APIView):
    permission_classes = [IsTenantAdmin]

    def delete(self, request, key_id):
        try:
            key = APIKey.objects.get(id=key_id, tenant=request.tenant)
        except APIKey.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        key.is_active = False
        key.save(update_fields=["is_active"])

        log_event(
            request.tenant,
            LogEventType.API_KEY_REVOKED,
            request=request,
            metadata={"name": key.name, "key_prefix": key.key_prefix},
        )
        return Response(status=204)
