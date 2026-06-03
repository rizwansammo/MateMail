import hashlib

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.models import MemberRole, MemberStatus, TenantMembership
from apps.tenants.permissions import HasTenantAccess, IsTenantAdmin
from .models import APIKey, TeamInvite
from .serializers import (
    APIKeyCreateSerializer,
    APIKeySerializer,
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
                    {"detail": "That person is already an active member of this workspace."},
                    status=400,
                )

        raw, invite = TeamInvite.make(request.tenant, email, role, request.user)
        accept_url = f"{settings.FRONTEND_URL}/accept-invite?token={raw}"

        role_display = role.replace("_", " ")
        inviter = request.user.full_name or request.user.email
        send_mail(
            subject=f"You've been invited to {request.tenant.name} on MateMail",
            message=(
                f"Hi,\n\n"
                f"{inviter} has invited you to join the {request.tenant.name} workspace "
                f"on MateMail as {role_display}.\n\n"
                f"Accept your invitation (expires in 7 days):\n{accept_url}\n\n"
                f"If you don't have a MateMail account yet, you'll be able to create one "
                f"after clicking the link.\n\n"
                f"— The MateMail team"
            ),
            from_email=f"MateMail <noreply@{settings.MAIL_DOMAIN}>",
            recipient_list=[email],
            fail_silently=True,
        )

        return Response(TeamInviteSerializer(invite).data, status=201)


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

        existing = TenantMembership.objects.filter(
            tenant=invite.tenant, user=request.user
        ).first()
        if existing:
            if existing.status == MemberStatus.ACTIVE:
                invite.accepted_at = timezone.now()
                invite.save(update_fields=["accepted_at"])
                return Response(
                    {"detail": "Already a member.", "tenant_id": str(invite.tenant_id)},
                    status=200,
                )
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

        return Response(
            {
                "detail": "Invite accepted.",
                "tenant_id": str(invite.tenant_id),
                "tenant_name": invite.tenant.name,
            }
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
                "valid": invite.is_pending,
                "email": invite.email,
                "role": invite.role,
                "tenant_name": invite.tenant.name,
                "invited_by": invite.invited_by.full_name or invite.invited_by.email if invite.invited_by else None,
                "expires_at": invite.expires_at,
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

        raw, api_key = APIKey.make(request.tenant, name, request.user, expires_at)

        data = APIKeySerializer(api_key).data
        data["key"] = raw  # Shown ONCE at creation — not stored
        return Response(data, status=201)


class APIKeyRevokeView(APIView):
    permission_classes = [IsTenantAdmin]

    def delete(self, request, key_id):
        try:
            key = APIKey.objects.get(id=key_id, tenant=request.tenant)
        except APIKey.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        key.is_active = False
        key.save(update_fields=["is_active"])
        return Response(status=204)
