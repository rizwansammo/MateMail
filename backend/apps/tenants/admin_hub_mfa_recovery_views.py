"""Owner initiated Hub administrator MFA recovery with recipient confirmation.

The operator can only request recovery. The recipient must affirm it from an
independently verified recovery mailbox. Neither participant receives the
other's credentials or a session token. This never touches PostBox accounts.
"""
import secrets
from datetime import timedelta

import pyotp
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.mailer import send_transactional
from apps.accounts.models import (
    TwoFactorBackupCode, TwoFactorSetup, VerifiedRecoveryEmail,
    RecoveryLoginChallenge, User,
)
from apps.accounts.tokens import hash_token, revoke_all_refresh_tokens
from apps.accounts.totp_crypto import decrypt_secret
from apps.logs.models import MailLog
from apps.security import ratelimit
from apps.security.limits import (
    ADMIN_MFA_RECOVERY_PER_OWNER, ADMIN_MFA_RECOVERY_PER_TARGET,
    TOTP_REPLAY_TTL,
)
from apps.security.throttling import AuthEndpointThrottle, AuthenticatedActionThrottle
from apps.teams.models import APIKey

from .admin_hub_mfa_policy import owner_can_manage_admin_hub_mfa
from .host_binding import scope_memberships, tenant_matches_request
from .models import AdminHubMfaRecoveryTicket, MemberStatus, TenantMembership


class RecoveryRequestInput(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    authenticator_code = serializers.RegexField(regex=r"^[0-9]{6}$", write_only=True)
    reason = serializers.CharField(min_length=10, max_length=500)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != {
            "current_password", "authenticator_code", "reason"
        }:
            raise serializers.ValidationError({"detail": "Unexpected or missing input."})
        return super().to_internal_value(data)


class RecoveryConfirmInput(serializers.Serializer):
    token = serializers.CharField(min_length=32, max_length=256)
    confirm = serializers.BooleanField()

    def validate_confirm(self, value):
        if value is not True:
            raise serializers.ValidationError("Explicit confirmation required.")
        return value


def _eligible_contact(user):
    return VerifiedRecoveryEmail.objects.filter(
        user=user, verified_at__isnull=False,
    ).exclude(email="").first()


def _enabled():
    return (
        bool(getattr(settings, "HUB_ADMIN_MFA_POLICY_ENABLED", False))
        and bool(getattr(settings, "HUB_ADMIN_MFA_RECOVERY_ENABLED", False))
    )


def _rate(bucket, key):
    return ratelimit.hit(bucket.bucket, str(key), limit=bucket.limit, window=bucket.window).allowed


class WorkspaceAdminHubMfaRecoveryRequestView(APIView):
    """Only the real current owner can initiate the external approval email."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthenticatedActionThrottle]
    http_method_names = ["post", "options"]

    def post(self, request, pk, member_id):
        if not _enabled():
            return Response({"detail": "Admin recovery is not activated."}, status=503)
        if isinstance(request.auth, APIKey) or getattr(request, "_mm_api_key", None):
            return Response({"detail": "An interactive owner session is required."}, status=403)

        actor = scope_memberships(
            request, TenantMembership.objects.select_related("tenant").filter(
                tenant_id=pk, user=request.user, status=MemberStatus.ACTIVE,
            ),
        ).first()
        target = scope_memberships(
            request, TenantMembership.objects.select_related("tenant", "user").filter(
                tenant_id=pk, pk=member_id, status=MemberStatus.ACTIVE,
            ),
        ).first()
        if not owner_can_manage_admin_hub_mfa(actor, target):
            return Response({"detail": "Not found."}, status=404)
        payload = RecoveryRequestInput(data=request.data)
        payload.is_valid(raise_exception=True)

        if not _rate(ADMIN_MFA_RECOVERY_PER_OWNER, request.user.pk) or not _rate(
            ADMIN_MFA_RECOVERY_PER_TARGET, target.user_id
        ):
            return Response({"detail": "Too many recovery requests. Try later."}, status=429)

        value = payload.validated_data
        owner = request.user
        setup = TwoFactorSetup.objects.filter(user=owner).first()
        code = value["authenticator_code"]
        if not owner.two_factor_enabled or not owner.check_password(
            value["current_password"]
        ) or setup is None or not pyotp.TOTP(decrypt_secret(
            setup.totp_secret
        )).verify(code, valid_window=1):
            return Response({"detail": "Current owner password and 2FA proof required."}, status=403)
        if not ratelimit.claim_once(
            "2fa:totp", f"{owner.pk}:{code}", ttl=TOTP_REPLAY_TTL
        ):
            return Response({"detail": "Authenticator code already used."}, status=403)
        if not target.user.two_factor_enabled or target.user.hub_mfa_reenrollment_required:
            return Response({"detail": "No active authenticator to recover."}, status=409)

        contact = _eligible_contact(target.user)
        if not contact or contact.email.lower() == target.user.email.lower():
            return Response({
                "detail": "This admin has no verified independent recovery email. Use a separately approved identity recovery process."
            }, status=409)

        raw = secrets.token_urlsafe(32)
        nonce_digest = hash_token(raw)
        now = timezone.now()
        with transaction.atomic():
            # Re-check authority while holding locks; no fabricated owner role
            # or removed admin can be used to affect a real account.
            live_actor = TenantMembership.objects.select_for_update().select_related(
                "tenant"
            ).get(pk=actor.pk)
            live_target = TenantMembership.objects.select_for_update().select_related(
                "user"
            ).get(pk=target.pk)
            if not owner_can_manage_admin_hub_mfa(live_actor, live_target):
                return Response({"detail": "Not found."}, status=404)
            AdminHubMfaRecoveryTicket.objects.filter(
                target_membership=live_target, consumed_at__isnull=True,
                cancelled_at__isnull=True,
            ).update(cancelled_at=now)
            row = AdminHubMfaRecoveryTicket.objects.create(
                requested_by=owner, target_membership=live_target,
                nonce_hash=nonce_digest, recipient_email=contact.email,
                reason=value["reason"], expires_at=now + timedelta(minutes=30),
            )

        # URL fragment is never sent in HTTP referrers or initial GET request.
        link = f"{settings.FRONTEND_URL.rstrip('/')}/recover-admin#token={raw}"
        delivered = send_transactional(
            subject="Approve MateMail administrator authenticator recovery",
            body=(
                f"A recovery request for your MateMail Hub administrator account "
                f"was initiated by your workspace owner.\n\n"
                f"To approve resetting your authenticator, open this link within "
                f"30 minutes:\n{link}\n\n"
                f"Only approve if you requested help. This link does not sign "
                f"anyone in; your normal password and a new authenticator "
                f"will be required afterward.\n"
                f"If you did not request recovery, ignore this message and "
                f"contact your workspace owner."
            ),
            to=[contact.email], purpose="hub_admin_mfa_recovery",
        )
        if not delivered:
            AdminHubMfaRecoveryTicket.objects.filter(pk=row.pk).update(
                cancelled_at=timezone.now(),
            )
        MailLog.objects.create(
            tenant=actor.tenant, source=owner.email,
            event_type="hub_admin_mfa_recovery_requested",
            result="success" if delivered else "delivery_failed",
            metadata={
                "actor_user_id": str(owner.pk),
                "target_user_id": str(target.user_id),
                "ticket_id": str(row.pk), "reason": value["reason"],
            },
        )
        if not delivered:
            return Response({"detail": "Recovery message delivery failed. No authenticator changed."}, status=503)
        return Response({
            "detail": "Approval instructions sent to the administrator's verified independent recovery email.",
            "expires_in_minutes": 30,
        }, status=202)


class WorkspaceAdminHubMfaRecoveryConfirmView(APIView):
    """Recipient controlled, one-time confirmation without an admin login."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthEndpointThrottle]
    http_method_names = ["post", "options"]

    def post(self, request):
        if not _enabled():
            return Response({"detail": "Admin recovery is not activated."}, status=503)
        payload = RecoveryConfirmInput(data=request.data)
        payload.is_valid(raise_exception=True)

        with transaction.atomic():
            ticket = AdminHubMfaRecoveryTicket.objects.select_for_update().select_related(
                "target_membership__tenant", "target_membership__user", "requested_by",
            ).filter(nonce_hash=hash_token(payload.validated_data["token"])).first()
            if (ticket is None or ticket.consumed_at is not None or
                    ticket.cancelled_at is not None or ticket.expires_at <= timezone.now()):
                return Response({"detail": "Recovery link is invalid or expired."}, status=401)

            member = TenantMembership.objects.select_for_update().select_related(
                "tenant", "user"
            ).get(pk=ticket.target_membership_id)
            actor = TenantMembership.objects.select_for_update().select_related(
                "tenant"
            ).filter(tenant=member.tenant, user=ticket.requested_by,
                     status=MemberStatus.ACTIVE).first()
            if (not tenant_matches_request(request, member.tenant) or
                    not owner_can_manage_admin_hub_mfa(actor, member)):
                return Response({"detail": "Recovery is not authorized."}, status=403)

            user = User.objects.select_for_update().get(pk=member.user_id)
            contact = _eligible_contact(user)
            if (not user.is_active or not user.two_factor_enabled or
                    user.hub_mfa_reenrollment_required or contact is None or
                    contact.email.lower() != ticket.recipient_email.lower() or
                    contact.email.lower() == user.email.lower()):
                return Response({"detail": "Recovery conditions have changed."}, status=409)

            # Same transaction: all credentials, versions, logs and the one-time
            # ticket must either commit together or remain untouched.
            TwoFactorBackupCode.objects.filter(user=user).delete()
            TwoFactorSetup.objects.filter(user=user).delete()
            RecoveryLoginChallenge.objects.filter(user=user, consumed_at__isnull=True).update(
                consumed_at=timezone.now(),
            )
            user.two_factor_enabled = False
            user.hub_mfa_reenrollment_required = True
            user.hub_security_epoch += 1
            user.save(update_fields=[
                "two_factor_enabled", "hub_mfa_reenrollment_required",
                "hub_security_epoch",
            ])
            revoked = revoke_all_refresh_tokens(user)
            # Machine API keys issued by this admin must not outlive a
            # credential compromise or be used to bypass forced reenrollment.
            # Recovery increments the user's account-wide security epoch.
            # Machine keys for this same user across other workspaces must not
            # survive either; keys issued by OTHER users remain untouched.
            api_keys_revoked = APIKey.objects.filter(
                created_by=user, is_active=True,
            ).update(is_active=False)
            ticket.consumed_at = timezone.now()
            ticket.save(update_fields=["consumed_at"])
            MailLog.objects.create(
                tenant=member.tenant, source=user.email,
                event_type="hub_admin_mfa_recovery_completed",
                result="success",
                metadata={
                    "actor_user_id": str(ticket.requested_by_id),
                    "target_user_id": str(user.pk), "ticket_id": str(ticket.pk),
                    "revoked_refresh_tokens": revoked,
                    "revoked_api_keys": api_keys_revoked,
                },
            )
        # Alerts contain no recovery token or sensitive credential.
        for address in {user.email, ticket.requested_by.email}:
            send_transactional(
                subject="MateMail administrator 2FA recovery completed",
                body=(
                    "An administrator authenticator reset was completed. All old "
                    "backup codes and authenticator settings are invalid. "
                    "Existing sessions and administrator-created API keys are revoked. The administrator must sign "
                    "in with their existing account password and set up a new "
                    "authenticator before accessing the Hub. "
                    "If unexpected, contact your organization security owner."
                ),
                to=[address], purpose="hub_admin_mfa_recovery_alert",
            )
        return Response({
            "detail": "Authenticator reset confirmed. Sign in with your password to set up a new authenticator.",
            "requires_new_enrollment": True,
        })
