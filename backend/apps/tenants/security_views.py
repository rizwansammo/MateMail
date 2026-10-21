"""Phase 7A gated organization MFA policy API.

No authentication changes are made in this initial foundation commit.
REQUIRE remains impossible to activate until the complete enforcement path is
implemented, audited, and independently enabled.
"""
from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .host_binding import scope_memberships
from .models import MemberRole, MemberStatus, OrganizationTwoFactorMode, TenantMembership
from .security_policy import can_activate_require, effective_two_factor_policy


class PolicyInput(serializers.Serializer):
    policy = serializers.ChoiceField(choices=OrganizationTwoFactorMode.choices)
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != {"policy", "current_password"}:
            raise serializers.ValidationError({"detail": "Exact policy and current_password are required."})
        return super().to_internal_value(data)


class WorkspaceTwoFactorPolicyView(APIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def membership(self, request, pk):
        # Scope by authenticated session and customer hostname before role test.
        # Never look up a tenant globally and authorize against a client-provided ID.
        return scope_memberships(
            request,
            TenantMembership.objects.select_related("tenant").filter(
                tenant_id=pk, user=request.user, status=MemberStatus.ACTIVE,
            ),
        ).first()

    def get(self, request, pk):
        membership = self.membership(request, pk)
        if not membership:
            return Response({"detail": "Not found."}, status=404)
        tenant = membership.tenant
        return Response({
            "policy": tenant.two_factor_policy,
            "effective_policy": effective_two_factor_policy(tenant),
            "configurable": bool(getattr(settings, "ORGANIZATION_2FA_POLICY_ENABLED", False)),
            "require_ready": can_activate_require(),
        })

    def patch(self, request, pk):
        if not getattr(settings, "ORGANIZATION_2FA_POLICY_ENABLED", False):
            return Response({"detail": "Organization 2FA policy is not available."}, status=503)
        membership = self.membership(request, pk)
        if not membership:
            return Response({"detail": "Not found."}, status=404)
        if membership.role not in (MemberRole.OWNER, MemberRole.ADMIN):
            return Response({"detail": "An organization administrator is required."}, status=403)
        # API keys may represent an organization administrator. They must not
        # change interactive authentication policy without a human password.
        from apps.teams.models import APIKey
        if isinstance(getattr(request, "auth", None), APIKey) or getattr(request, "_mm_api_key", None):
            return Response({"detail": "A user session is required."}, status=403)
        payload = PolicyInput(data=request.data)
        payload.is_valid(raise_exception=True)
        if not request.user.check_password(payload.validated_data["current_password"]):
            return Response({"detail": "Reauthentication failed."}, status=403)
        selected = payload.validated_data["policy"]
        if selected == OrganizationTwoFactorMode.REQUIRE and not can_activate_require():
            return Response({
                "detail": "Require 2FA cannot be activated before end-to-end enforcement and recovery are ready."
            }, status=409)
        from apps.domains.models import Domain
        from apps.mail_engine.errors import MailEngineError
        from apps.mail_engine.factory import get_adapter
        from apps.mail_engine.native_adapter import NativeMailEngineAdapter

        def native_policy(tenant, required):
            # Only the verified, Native-provisioned domain set can be bound.
            # Newly provisioned domains carry tenant_id from DomainSpec, so
            # the Native policy automatically applies before the first login.
            domains = list(Domain.objects.filter(
                tenant=tenant, mail_engine_provisioned=True,
            ).order_by("domain").values_list("domain", flat=True))
            adapter = get_adapter()
            if not isinstance(adapter, NativeMailEngineAdapter):
                raise RuntimeError("Organization Require needs Native Mail Engine")
            return adapter.set_tenant_mfa_required(str(tenant.pk), domains, required)

        # NEVER issue a customer-facing Require success until Dovecot's native
        # protocol gate commits first. A subsequent Django failure leaves
        # Native MORE restrictive, never a permissive SMTP/IMAP bypass.
        try:
            with transaction.atomic():
                tenant = membership.tenant.__class__.objects.select_for_update().get(
                    pk=membership.tenant_id,
                )
                was_required = tenant.two_factor_policy == OrganizationTwoFactorMode.REQUIRE
                if selected == OrganizationTwoFactorMode.REQUIRE:
                    if not (getattr(settings, "POSTBOX_2FA_ENABLED", False)
                            and getattr(settings, "SMTP_IMAP_SECURITY_ENABLED", False)
                            and getattr(settings, "ORGANIZATION_2FA_ENFORCEMENT_READY", False)):
                        return Response({"detail": "Organization enforcement is not ready."}, status=503)
                    native_policy(tenant, True)
                tenant.two_factor_policy = selected
                tenant.save(update_fields=["two_factor_policy", "updated_at"])
        except (MailEngineError, RuntimeError):
            return Response({
                "detail": "Could not confirm SMTP/IMAP enforcement. Organization policy was not changed."
            }, status=503)

        if was_required and selected != OrganizationTwoFactorMode.REQUIRE:
            # Removal order is intentionally the opposite of enabling Require:
            # first commit the less restrictive Django policy, then remove
            # Native enforcement. A Native failure leaves legacy clients
            # temporarily blocked (safe), not a policy bypass.
            try:
                native_policy(tenant, False)
            except (MailEngineError, RuntimeError):
                return Response({
                    "detail": "Organization policy changed, but external mail-client access remains restricted. Retry this action."
                }, status=503)
        return Response({
            "policy": selected,
            "effective_policy": effective_two_factor_policy(tenant),
        })
