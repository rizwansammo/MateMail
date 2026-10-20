"""Workspace owner administration of Hub administrator MFA policy.

This does not reset credentials, expose TOTP seeds or affect PostBox mailboxes.
All mutation is disabled until a separate Hub-only release flag is enabled.
"""
import pyotp

from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import TwoFactorSetup
from apps.accounts.totp_crypto import decrypt_secret
from apps.logs.models import MailLog
from apps.security import ratelimit
from apps.security.limits import TOTP_REPLAY_TTL, TWO_FACTOR_MANAGE_PER_USER

from .admin_hub_mfa_policy import (
    configured_admin_hub_mfa_policy,
    effective_admin_hub_mfa_policy,
    owner_can_manage_admin_hub_mfa,
)
from .host_binding import scope_memberships
from .models import AdminHubMfaMode, MemberRole, MemberStatus, TenantMembership


class PolicyChangeInput(serializers.Serializer):
    policy = serializers.ChoiceField(choices=AdminHubMfaMode.choices)
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    authenticator_code = serializers.RegexField(regex=r"^[0-9]{6}$", write_only=True)
    reason = serializers.CharField(min_length=5, max_length=500, trim_whitespace=True)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != {
            "policy", "current_password", "authenticator_code", "reason"
        }:
            raise serializers.ValidationError({"detail": "Unexpected or missing input."})
        return super().to_internal_value(data)


class WorkspaceAdminHubMfaAvailabilityView(APIView):
    """Reveal only whether Hub admin management has been released."""
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        actor = scope_memberships(
            request,
            TenantMembership.objects.select_related("tenant").filter(
                tenant_id=pk, user=request.user, status=MemberStatus.ACTIVE,
            ),
        ).first()
        if actor is None:
            return Response({"detail": "Not found."}, status=404)
        return Response({
            "configurable": (
                actor.role == MemberRole.OWNER and
                actor.tenant.owner_id == request.user.pk and
                bool(getattr(settings, "HUB_ADMIN_MFA_POLICY_ENABLED", False))
            ),
            "require_ready": bool(
                getattr(settings, "HUB_ADMIN_MFA_ENFORCEMENT_READY", False)
            ),
        })


class WorkspaceAdminHubMfaPolicyView(APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def _resolve(self, request, pk, member_id):
        actor = scope_memberships(
            request,
            TenantMembership.objects.select_related("tenant").filter(
                tenant_id=pk, user=request.user, status=MemberStatus.ACTIVE,
            ),
        ).first()
        target = scope_memberships(
            request,
            TenantMembership.objects.select_related("tenant", "user").filter(
                tenant_id=pk, pk=member_id, status=MemberStatus.ACTIVE,
            ),
        ).first()
        if not owner_can_manage_admin_hub_mfa(actor, target):
            return None
        return actor, target

    def _guard(self, request):
        if not getattr(settings, "HUB_ADMIN_MFA_POLICY_ENABLED", False):
            return Response({"detail": "Hub administrator MFA management is not activated."}, status=503)
        # A machine API key is not a human owner approving a security policy.
        if getattr(request, "_mm_api_key", None) is not None or getattr(
            request.auth, "key_hash", None
        ) is not None:
            return Response({"detail": "An interactive user session is required."}, status=403)
        return None

    def get(self, request, pk, member_id):
        denied = self._guard(request)
        if denied is not None:
            return denied
        resolved = self._resolve(request, pk, member_id)
        if resolved is None:
            return Response({"detail": "Not found."}, status=404)
        _, target = resolved
        return Response({
            "member_id": str(target.pk),
            "email": target.user.email,
            "role": target.role,
            "policy": configured_admin_hub_mfa_policy(target),
            "effective_policy": effective_admin_hub_mfa_policy(target),
            "two_factor_enabled": target.user.two_factor_enabled,
            "require_ready": bool(getattr(settings, "HUB_ADMIN_MFA_ENFORCEMENT_READY", False)),
        })

    def patch(self, request, pk, member_id):
        denied = self._guard(request)
        if denied is not None:
            return denied
        resolved = self._resolve(request, pk, member_id)
        if resolved is None:
            return Response({"detail": "Not found."}, status=404)
        actor, target = resolved
        payload = PolicyChangeInput(data=request.data)
        payload.is_valid(raise_exception=True)
        value = payload.validated_data
        selected = value["policy"]
        if selected == AdminHubMfaMode.REQUIRED and not getattr(
            settings, "HUB_ADMIN_MFA_ENFORCEMENT_READY", False
        ):
            return Response({"detail": "Required MFA cannot be activated before recovery readiness approval."}, status=409)

        owner = request.user
        rate = ratelimit.check(
            TWO_FACTOR_MANAGE_PER_USER.bucket, owner.pk,
            limit=TWO_FACTOR_MANAGE_PER_USER.limit,
            window=TWO_FACTOR_MANAGE_PER_USER.window,
        )
        if not rate.allowed:
            return Response({"detail": "Too many authentication attempts."}, status=429)
        if not owner.two_factor_enabled or not owner.check_password(value["current_password"]):
            ratelimit.hit(
                TWO_FACTOR_MANAGE_PER_USER.bucket, owner.pk,
                limit=TWO_FACTOR_MANAGE_PER_USER.limit,
                window=TWO_FACTOR_MANAGE_PER_USER.window,
            )
            return Response({"detail": "Owner password and authenticator verification required."}, status=403)
        setup = TwoFactorSetup.objects.filter(user=owner).first()
        code = value["authenticator_code"]
        if setup is None or not pyotp.TOTP(decrypt_secret(setup.totp_secret)).verify(
            code, valid_window=1
        ):
            ratelimit.hit(
                TWO_FACTOR_MANAGE_PER_USER.bucket, owner.pk,
                limit=TWO_FACTOR_MANAGE_PER_USER.limit,
                window=TWO_FACTOR_MANAGE_PER_USER.window,
            )
            return Response({"detail": "Owner password and authenticator verification required."}, status=403)
        if not ratelimit.claim_once(
            "2fa:totp", f"{owner.pk}:{code}", ttl=TOTP_REPLAY_TTL,
        ):
            return Response({"detail": "Authenticator code already used."}, status=403)

        with transaction.atomic():
            locked_actor = TenantMembership.objects.select_for_update().select_related("tenant").get(
                pk=actor.pk
            )
            locked_target = TenantMembership.objects.select_for_update().select_related("user").get(
                pk=target.pk
            )
            if not owner_can_manage_admin_hub_mfa(locked_actor, locked_target):
                return Response({"detail": "Not found."}, status=404)
            previous = locked_target.admin_hub_mfa_policy
            locked_target.admin_hub_mfa_policy = selected
            locked_target.save(update_fields=["admin_hub_mfa_policy", "updated_at"])
            MailLog.objects.create(
                tenant=locked_actor.tenant, event_type="hub_admin_mfa_policy_changed",
                result="success", source=owner.email,
                metadata={
                    "actor_user_id": str(owner.pk),
                    "target_user_id": str(locked_target.user_id),
                    "member_id": str(locked_target.pk),
                    "old_policy": previous, "new_policy": selected,
                    "reason": value["reason"],
                },
            )
        return Response({
            "member_id": str(target.pk),
            "policy": selected,
            "effective_policy": effective_admin_hub_mfa_policy(locked_target),
            "two_factor_enabled": locked_target.user.two_factor_enabled,
        })
