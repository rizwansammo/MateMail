import logging

from django.db.models import Sum
from django.utils.text import slugify
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import WorkspaceSwitchSerializer
from apps.accounts.tokens import make_tokens
from .models import MemberRole, MemberStatus, Tenant, TenantMembership, TenantStatus
from .permissions import IsTenantAdmin, IsTenantOwner
from .serializers import (
    MemberInviteSerializer,
    MemberRoleUpdateSerializer,
    TenantCreateSerializer,
    TenantMembershipSerializer,
    TenantSerializer,
    WorkspaceDetailSerializer,
)

logger = logging.getLogger(__name__)


class WorkspaceListView(APIView):
    """List all workspaces the authenticated user belongs to."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        memberships = (
            TenantMembership.objects
            .select_related("tenant")
            .filter(user=request.user, status="active")
            .order_by("created_at")
        )
        data = [
            {
                "id": str(m.tenant.id),
                "name": m.tenant.name,
                "slug": m.tenant.slug,
                "status": m.tenant.status,
                "plan": m.tenant.plan,
                "role": m.role,
            }
            for m in memberships
        ]
        return Response(data)


class WorkspaceCreateView(APIView):
    """Create an additional workspace for the authenticated user."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TenantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"]

        slug = _unique_slug(slugify(name))
        tenant = Tenant.objects.create(
            name=name,
            slug=slug,
            owner=request.user,
            status=TenantStatus.TRIAL,
        )
        TenantMembership.objects.create(
            tenant=tenant,
            user=request.user,
            role=MemberRole.OWNER,
            status=MemberStatus.ACTIVE,
        )
        tokens = make_tokens(request.user, tenant_id=tenant.id)
        return Response(
            {**tokens, "tenant": TenantSerializer(tenant).data},
            status=201,
        )


class WorkspaceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_tenant(self, request, pk):
        try:
            mem = TenantMembership.objects.select_related("tenant").get(
                tenant_id=pk, user=request.user, status="active"
            )
            return mem.tenant
        except TenantMembership.DoesNotExist:
            return None

    def get(self, request, pk):
        tenant = self._get_tenant(request, pk)
        if not tenant:
            return Response({"detail": "Not found."}, status=404)
        return Response(WorkspaceDetailSerializer(tenant, context={"request": request}).data)

    def patch(self, request, pk):
        tenant = self._get_tenant(request, pk)
        if not tenant:
            return Response({"detail": "Not found."}, status=404)

        # Only owner/admin may rename the workspace
        mem = TenantMembership.objects.get(tenant=tenant, user=request.user)
        if mem.role not in (MemberRole.OWNER, MemberRole.ADMIN):
            return Response({"detail": "Permission denied."}, status=403)

        name = request.data.get("name", "").strip()
        if name:
            tenant.name = name
            tenant.save(update_fields=["name", "updated_at"])

        return Response(WorkspaceDetailSerializer(tenant, context={"request": request}).data)


class WorkspaceSwitchView(APIView):
    """Re-issue tokens with a different tenant_id claim."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WorkspaceSwitchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant_id = serializer.validated_data["tenant_id"]

        try:
            membership = TenantMembership.objects.select_related("tenant").get(
                tenant_id=tenant_id, user=request.user, status="active"
            )
        except TenantMembership.DoesNotExist:
            return Response({"detail": "Workspace not found or access denied."}, status=404)

        tokens = make_tokens(request.user, tenant_id=tenant_id)
        return Response(
            {
                **tokens,
                "tenant": {
                    "id": str(membership.tenant.id),
                    "name": membership.tenant.name,
                    "slug": membership.tenant.slug,
                    "status": membership.tenant.status,
                    "role": membership.role,
                },
            }
        )


class OnboardingStatusView(APIView):
    """Returns a checklist of completed onboarding steps for a workspace."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            mem = TenantMembership.objects.select_related("tenant").get(
                tenant_id=pk, user=request.user, status="active"
            )
        except TenantMembership.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        tenant = mem.tenant
        first_domain = tenant.domains.first()
        domain_verified = (
            first_domain.status == "active" if first_domain else False
        )

        return Response(
            {
                "workspace_created": True,
                "domain_added": first_domain is not None,
                "dns_verified": domain_verified,
                "first_mailbox_created": tenant.mailboxes.exists(),
            }
        )


class WorkspaceStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            mem = TenantMembership.objects.select_related("tenant").get(
                tenant_id=pk, user=request.user, status="active"
            )
        except TenantMembership.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        tenant = mem.tenant
        domain_qs = tenant.domains.all()
        mailbox_qs = tenant.mailboxes.all()
        storage = mailbox_qs.aggregate(used=Sum("storage_used_mb"), quota=Sum("quota_mb"))

        return Response({
            "domain_count": domain_qs.count(),
            "active_domain_count": domain_qs.filter(status="active").count(),
            "mailbox_count": mailbox_qs.count(),
            "active_mailbox_count": mailbox_qs.filter(status="active").count(),
            "storage_used_mb": storage["used"] or 0,
            "storage_quota_mb": storage["quota"] or 0,
            "member_count": tenant.memberships.filter(status="active").count(),
            "my_role": mem.role,
            "tenant_status": tenant.status,
            "tenant_plan": tenant.plan,
        })


class WorkspaceMemberListView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_my_membership(self, request, pk):
        try:
            return TenantMembership.objects.select_related("tenant").get(
                tenant_id=pk, user=request.user, status="active"
            )
        except TenantMembership.DoesNotExist:
            return None

    def get(self, request, pk):
        if not self._get_my_membership(request, pk):
            return Response({"detail": "Not found."}, status=404)
        members = (
            TenantMembership.objects
            .select_related("user")
            .filter(tenant_id=pk, status__in=["active", "invited"])
            .order_by("created_at")
        )
        return Response(TenantMembershipSerializer(members, many=True).data)

    def post(self, request, pk):
        my_mem = self._get_my_membership(request, pk)
        if not my_mem:
            return Response({"detail": "Not found."}, status=404)
        if my_mem.role not in (MemberRole.OWNER, MemberRole.ADMIN):
            return Response({"detail": "Permission denied."}, status=403)

        serializer = MemberInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        role = serializer.validated_data["role"]

        from apps.accounts.models import User
        try:
            target_user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({"detail": "No MateMail account with that email address."}, status=404)

        if target_user == request.user:
            return Response({"detail": "You are already a member of this workspace."}, status=400)

        existing = TenantMembership.objects.filter(tenant_id=pk, user=target_user).first()
        if existing:
            if existing.status == MemberStatus.ACTIVE:
                return Response({"detail": "That user is already a member."}, status=400)
            existing.role = role
            existing.status = MemberStatus.ACTIVE
            existing.invited_by = request.user
            existing.save(update_fields=["role", "status", "invited_by", "updated_at"])
            return Response(TenantMembershipSerializer(existing).data)

        new_mem = TenantMembership.objects.create(
            tenant_id=pk,
            user=target_user,
            role=role,
            status=MemberStatus.ACTIVE,
            invited_by=request.user,
        )
        return Response(TenantMembershipSerializer(new_mem).data, status=201)


class WorkspaceMemberDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_my_membership(self, request, pk):
        try:
            return TenantMembership.objects.get(tenant_id=pk, user=request.user, status="active")
        except TenantMembership.DoesNotExist:
            return None

    def _get_target(self, pk, member_id):
        try:
            return TenantMembership.objects.select_related("user").get(id=member_id, tenant_id=pk)
        except TenantMembership.DoesNotExist:
            return None

    def patch(self, request, pk, member_id):
        my_mem = self._get_my_membership(request, pk)
        if not my_mem:
            return Response({"detail": "Not found."}, status=404)
        if my_mem.role != MemberRole.OWNER:
            return Response({"detail": "Only the workspace owner can change roles."}, status=403)

        target = self._get_target(pk, member_id)
        if not target:
            return Response({"detail": "Not found."}, status=404)
        if target.user == request.user:
            return Response({"detail": "Cannot change your own role."}, status=400)
        if target.role == MemberRole.OWNER:
            return Response({"detail": "Cannot change the owner's role."}, status=400)

        serializer = MemberRoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_role = serializer.validated_data["role"]
        if new_role == MemberRole.OWNER:
            return Response({"detail": "Cannot assign the owner role."}, status=400)

        target.role = new_role
        target.save(update_fields=["role", "updated_at"])
        return Response(TenantMembershipSerializer(target).data)

    def delete(self, request, pk, member_id):
        my_mem = self._get_my_membership(request, pk)
        if not my_mem:
            return Response({"detail": "Not found."}, status=404)

        target = self._get_target(pk, member_id)
        if not target:
            return Response({"detail": "Not found."}, status=404)
        if target.role == MemberRole.OWNER:
            return Response({"detail": "Cannot remove the workspace owner."}, status=400)
        if my_mem.role != MemberRole.OWNER and target.user != request.user:
            return Response({"detail": "Permission denied."}, status=403)

        target.status = MemberStatus.REMOVED
        target.save(update_fields=["status", "updated_at"])
        return Response(status=204)


def _unique_slug(base: str) -> str:
    slug = base or "workspace"
    counter = 1
    while Tenant.objects.filter(slug=slug).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug
