"""Platform Console: two-operator Workspace Owner emergency recovery.

The platform can never choose a target user, learn an OTP, set a usable
password, or obtain a Hub session. A case becomes actionable only after
independent staff approval, a 24 hour hold, and owner confirmation from a
previously verified external recovery mailbox. No automated bypass exists
when that mailbox is unavailable. See docs/PHASE4_OWNER_RECOVERY.md.
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import Throttled

from apps.accounts.mailer import send_transactional
from apps.accounts.models import (
    PasswordResetToken, RecoveryLoginChallenge, TwoFactorBackupCode,
    TwoFactorSetup, User, VerifiedRecoveryEmail,
)
from apps.accounts.tokens import hash_token, revoke_all_refresh_tokens
from apps.security import ratelimit
from apps.security.limits import OWNER_EMERGENCY_PER_ACTOR, OWNER_EMERGENCY_PER_OWNER
from apps.security.throttling import AuthEndpointThrottle
from apps.tenants.host_binding import bound_tenant_slug
from apps.tenants.models import Tenant
from apps.tenants.permissions import IsPlatformAdmin
from apps.teams.models import APIKey

from .audit import record_platform_action
from .models import OwnerEmergencyRecoveryCase

HOLD = timedelta(hours=24)
CASE_TTL = timedelta(days=7)
CAPABILITY_TTL = timedelta(minutes=30)


class StartCase(serializers.Serializer):
    mode = serializers.ChoiceField(choices=OwnerEmergencyRecoveryCase.Mode.choices)
    reason = serializers.CharField(min_length=20, max_length=500, trim_whitespace=True)
    evidence_reference = serializers.CharField(min_length=8, max_length=200, trim_whitespace=True)
    verification_note = serializers.CharField(min_length=20, max_length=500, trim_whitespace=True)
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != {
            "mode", "reason", "evidence_reference", "verification_note", "current_password"
        }:
            raise serializers.ValidationError({"detail": "Unexpected or missing input."})
        return super().to_internal_value(data)


class ReviewCase(serializers.Serializer):
    verification_note = serializers.CharField(min_length=20, max_length=500)
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)


class OperatorPassword(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)


class ConfirmOwner(serializers.Serializer):
    capability = serializers.CharField(min_length=32, max_length=256)
    confirm = serializers.BooleanField()

    def validate_confirm(self, value):
        if value is not True:
            raise serializers.ValidationError("Explicit owner confirmation required.")
        return value


def enabled():
    return bool(getattr(settings, "PLATFORM_OWNER_EMERGENCY_RECOVERY_ENABLED", False))


def _verified_platform_session(request):
    """Only a recently verified Platform Console session, not a Hub JWT."""
    token = request.auth
    issued = token.get("platform_otp_verified_at", 0) if hasattr(token, "get") else 0
    now = int(timezone.now().timestamp())
    return bool(
        request.user.is_active and request.user.is_platform_admin
        and not getattr(request, "_mm_api_key", None)
        and not isinstance(token, APIKey)
        and isinstance(issued, int) and 0 <= now - issued <= 900
    )


def _platform_operator(request, password):
    return _verified_platform_session(request) and request.user.check_password(password)


def _rate(actor_id, owner_id):
    for limit, key in (
        (OWNER_EMERGENCY_PER_ACTOR, actor_id),
        (OWNER_EMERGENCY_PER_OWNER, owner_id),
    ):
        result = ratelimit.hit(limit.bucket, str(key), limit=limit.limit, window=limit.window)
        if not result.allowed:
            raise Throttled(wait=result.retry_after, detail="Emergency recovery is rate limited.")


def _owner_contact(owner):
    contact = VerifiedRecoveryEmail.objects.filter(
        user=owner, verified_at__isnull=False
    ).exclude(email="").first()
    if (contact is None or not owner.is_active or owner.is_platform_admin or
            contact.email.strip().lower() == owner.email.strip().lower()):
        return None
    return contact


def _case_valid(case, tenant, *, require_factor=True):
    if (tenant.owner_id != case.owner_id or not case.owner.is_active or
            case.owner.is_platform_admin or
            case.owner.hub_security_epoch != case.owner_security_epoch):
        return False
    if require_factor and (
        not case.owner.two_factor_enabled or case.owner.hub_mfa_reenrollment_required
    ):
        return False
    contact = _owner_contact(case.owner)
    return bool(
        contact and contact.email.lower() == case.recovery_address.lower()
        and contact.verified_at == case.recovery_address_verified_at
    )


def _serial(case):
    now = timezone.now()
    status = (
        "completed" if case.completed_at else
        "cancelled" if case.cancelled_at else
        "expired" if case.expires_at <= now else
        "awaiting_owner" if case.dispatched_at else
        "security_hold" if case.approved_at else "pending_second_approval"
    )
    return {
        "id": str(case.pk), "mode": case.mode, "status": status,
        "reason": case.reason,
        "evidence_reference": case.identity_evidence_reference,
        "initiated_by": case.initiated_by.email,
        "approved_by": case.approved_by.email if case.approved_by_id else None,
        "created_at": case.created_at.isoformat(),
        "hold_until": case.hold_until.isoformat() if case.hold_until else None,
        "capability_expires_at": (
            case.capability_expires_at.isoformat()
            if case.capability_expires_at else None
        ),
        "completed_at": case.completed_at.isoformat() if case.completed_at else None,
    }


class PlatformOwnerEmergencyCasesView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    http_method_names = ["get", "post", "head", "options"]

    def get(self, request, pk):
        if not enabled():
            return Response({"detail": "Emergency recovery is disabled."}, status=503)
        if not _verified_platform_session(request):
            return Response({"detail": "A recent Platform Console security verification is required."}, status=403)
        tenant = Tenant.objects.filter(pk=pk).first()
        if tenant is None:
            return Response({"detail": "Not found."}, status=404)
        rows = OwnerEmergencyRecoveryCase.objects.select_related(
            "initiated_by", "approved_by",
        ).filter(tenant=tenant).order_by("-created_at")[:20]
        return Response({
            "owner_id": str(tenant.owner_id),
            "cases": [_serial(case) for case in rows],
            "min_hold_hours": int(HOLD.total_seconds() // 3600),
        })

    def post(self, request, pk):
        if not enabled():
            return Response({"detail": "Emergency recovery is disabled."}, status=503)
        tenant = Tenant.objects.select_related("owner").filter(pk=pk).first()
        if tenant is None:
            return Response({"detail": "Not found."}, status=404)
        value = StartCase(data=request.data)
        value.is_valid(raise_exception=True)
        data = value.validated_data
        owner = tenant.owner
        _rate(request.user.pk, owner.pk)
        if not _platform_operator(request, data["current_password"]):
            return Response({"detail": "Recent Platform Console MFA and password verification required."}, status=403)
        if owner.id == request.user.id:
            return Response({"detail": "An operator cannot recover their own account."}, status=403)
        contact = _owner_contact(owner)
        if contact is None or not owner.two_factor_enabled or owner.hub_mfa_reenrollment_required:
            return Response({
                "detail": "A verified independent owner recovery address and active authenticator are required. Use a separately approved offline identity recovery process."
            }, status=409)
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().select_related("owner").get(pk=tenant.pk)
            if tenant.owner_id != owner.id:
                return Response({"detail": "Ownership changed."}, status=409)
            if OwnerEmergencyRecoveryCase.objects.filter(
                tenant=tenant, completed_at__isnull=True, cancelled_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).exists():
                return Response({"detail": "An emergency case is already active."}, status=409)
            case = OwnerEmergencyRecoveryCase.objects.create(
                tenant=tenant, owner=owner, initiated_by=request.user,
                mode=data["mode"], reason=data["reason"],
                identity_evidence_reference=data["evidence_reference"],
                identity_verification_note=data["verification_note"],
                recovery_address=contact.email,
                recovery_address_verified_at=contact.verified_at,
                owner_security_epoch=owner.hub_security_epoch,
                expires_at=timezone.now() + CASE_TTL,
            )
            record_platform_action(
                actor=request.user, action="owner.emergency_started", request=request,
                tenant=tenant, target_user=owner, reason=case.reason,
                metadata={"case_id": str(case.id), "mode":case.mode,
                          "evidence_reference":case.identity_evidence_reference},
            )
        # Notices intentionally do not carry an approval capability.
        for address in {owner.email, contact.email}:
            send_transactional(
                subject="MateMail: Owner emergency recovery review started",
                body=f"A recovery review was opened for {tenant.name}. No credentials have changed. If unexpected, contact MateMail security immediately. Case reference: {case.id}.",
                to=[address], purpose="owner_emergency_notice",
            )
        return Response({"detail": "Case opened for independent security review.", "case": _serial(case)}, status=201)


class PlatformOwnerEmergencyCaseActionView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    http_method_names = ["post", "options"]

    def post(self, request, pk, case_id, action):
        if not enabled():
            return Response({"detail": "Emergency recovery is disabled."}, status=503)
        if action not in ("approve", "dispatch", "cancel"):
            return Response({"detail": "Not found."}, status=404)
        cls = ReviewCase if action == "approve" else OperatorPassword
        payload = cls(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        attempted = OwnerEmergencyRecoveryCase.objects.filter(
            tenant_id=pk, pk=case_id
        ).values_list("owner_id", flat=True).first()
        if attempted is None:
            return Response({"detail": "Not found."}, status=404)
        _rate(request.user.pk, attempted)
        if not _platform_operator(request, data["current_password"]):
            return Response({"detail": "Recent Platform Console MFA and password verification required."}, status=403)
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().select_related("owner").filter(pk=pk).first()
            case = OwnerEmergencyRecoveryCase.objects.select_for_update().select_related(
                "owner", "initiated_by",
            ).filter(tenant_id=pk, pk=case_id).first()
            if case is None or tenant is None:
                return Response({"detail": "Not found."}, status=404)
            if case.completed_at or case.cancelled_at or case.expires_at <= timezone.now():
                return Response({"detail": "Case is closed or expired."}, status=409)
            if action != "cancel" and not _case_valid(case, tenant):
                return Response({"detail": "Owner verification conditions have changed."}, status=409)
            if action == "approve":
                if case.approved_at:
                    return Response({"detail": "Already approved."}, status=409)
                if request.user.id in (case.initiated_by_id, case.owner_id):
                    return Response({"detail": "A different platform operator must approve."}, status=403)
                case.approved_by = request.user
                case.approved_at = timezone.now()
                case.hold_until = case.approved_at + HOLD
                case.reviewer_verification_note = data["verification_note"]
                case.save(update_fields=[
                    "approved_by", "approved_at", "hold_until",
                    "reviewer_verification_note",
                ])
                record_platform_action(
                    actor=request.user, action="owner.emergency_approved",
                    request=request, tenant=tenant, target_user=case.owner,
                    reason=data["verification_note"],
                    metadata={"case_id": str(case.pk), "mode":case.mode},
                )
                response = {"detail": "Second approval recorded. 24-hour security hold started.", "case":_serial(case)}
            elif action == "cancel":
                case.cancelled_at = timezone.now()
                case.save(update_fields=["cancelled_at"])
                record_platform_action(
                    actor=request.user, action="owner.emergency_cancelled",
                    request=request, tenant=tenant, target_user=case.owner,
                    reason="Cancelled by authorized platform security operator.",
                    metadata={"case_id": str(case.pk)},
                )
                response = {"detail": "Recovery case cancelled.", "case":_serial(case)}
            else:
                if not case.approved_by_id or not case.hold_until or case.hold_until > timezone.now():
                    return Response({"detail": "Independent approval and security hold are required."}, status=409)
                if case.dispatched_at:
                    return Response({"detail": "Approval message already issued."}, status=409)
                if request.user.id != case.approved_by_id:
                    return Response({"detail": "The independent reviewer must dispatch owner approval."}, status=403)
                raw = secrets.token_urlsafe(32)
                link = f"{settings.FRONTEND_URL.rstrip('/')}/owner-emergency#capability={raw}"
                delivered = send_transactional(
                    subject="MateMail: Confirm your workspace owner account recovery",
                    body=(
                        f"The independent security review for {tenant.name} was approved and "
                        f"the 24-hour hold completed. No credentials changed yet.\n\n"
                        f"Only if YOU requested this recovery, open the following link "
                        f"within 30 minutes:\n{link}\n\n"
                        f"Approval invalidates your current authenticator and all "
                        f"backup codes and sessions. Full recovery also makes "
                        f"your old password unusable and sends a one-use password reset "
                        f"link to THIS independently verified address.\n"
                        f"If unauthorized, do NOT approve. Contact support. Case {case.id}."
                    ),
                    to=[case.recovery_address], purpose="owner_emergency_approval",
                )
                if not delivered:
                    return Response({"detail": "Independent recovery email could not be delivered."}, status=503)
                case.capability_hash = hash_token(raw)
                case.dispatched_at = timezone.now()
                case.capability_expires_at = case.dispatched_at + CAPABILITY_TTL
                case.save(update_fields=["capability_hash", "dispatched_at", "capability_expires_at"])
                record_platform_action(
                    actor=request.user, action="owner.emergency_dispatched",
                    request=request, tenant=tenant, target_user=case.owner,
                    reason="Owner external confirmation invitation dispatched.",
                    metadata={"case_id": str(case.pk)},
                )
                response = {"detail": "Owner confirmation sent to previously verified external recovery address.", "case":_serial(case)}
        if action in ("approve", "cancel"):
            for addr in {case.owner.email, case.recovery_address}:
                send_transactional(
                    subject=f"MateMail: Owner emergency recovery {action}",
                    body=f"Security case {case.id} for {tenant.name} was {action}d. No owner credentials were changed. If unexpected, contact MateMail support.",
                    to=[addr], purpose="owner_emergency_notice",
                )
        return Response(response)


class OwnerEmergencyConfirmView(APIView):
    """Owner capability: no JWT, password or staff access granted by this endpoint."""
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthEndpointThrottle]
    http_method_names = ["post", "options"]

    def post(self, request):
        if not enabled():
            return Response({"detail": "Emergency recovery is disabled."}, status=503)
        if bound_tenant_slug(request):
            return Response({"detail": "Not found."}, status=404)
        payload = ConfirmOwner(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        try:
            with transaction.atomic():
                case = OwnerEmergencyRecoveryCase.objects.select_for_update().select_related(
                    "tenant", "owner", "initiated_by",
                ).filter(capability_hash=hash_token(data["capability"])).first()
                if (case is None or case.completed_at or case.cancelled_at or
                        not case.approved_by_id or case.expires_at <= timezone.now() or
                        not case.dispatched_at or
                        case.capability_expires_at <= timezone.now()):
                    return Response({"detail": "Invalid, expired or already used approval."}, status=401)
                tenant = Tenant.objects.select_for_update().select_related("owner").get(pk=case.tenant_id)
                owner = User.objects.select_for_update().get(pk=case.owner_id)
                if not _case_valid(case, tenant) or case.hold_until > timezone.now():
                    return Response({"detail": "Recovery conditions have changed."}, status=409)
                if (case.initiated_by_id == case.approved_by_id or
                        case.initiated_by_id == case.owner_id or
                        case.approved_by_id == case.owner_id or
                        not case.initiated_by.is_active or not case.initiated_by.is_platform_admin or
                        not case.approved_by.is_active or not case.approved_by.is_platform_admin):
                    return Response({"detail": "Security approvals are no longer valid."}, status=403)

                if case.mode == OwnerEmergencyRecoveryCase.Mode.FULL:
                    # Prepare a password-reset capability before removing any
                    # credential. A mail delivery failure rolls back everything.
                    PasswordResetToken.objects.filter(
                        user=owner, is_used=False
                    ).update(is_used=True)
                    raw, _ = PasswordResetToken.make(owner)
                    reset_url = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={raw}"
                    delivered = send_transactional(
                        subject="MateMail: Complete your owner password recovery",
                        body=(
                            f"Your verified full recovery for {tenant.name} was approved. "
                            f"Use this one-time password reset link within one hour:\n"
                            f"{reset_url}\n\nAfter setting your password, sign in and "
                            f"set up a NEW authenticator. If unexpected, alert security."
                        ),
                        to=[case.recovery_address],
                        purpose="owner_emergency_full_password_reset",
                    )
                    if not delivered:
                        raise RecoveryDeliveryFailed()

                # Invalidate old password-reset links as well: a link issued
                # before emergency recovery must never survive a credential
                # compromise, including for the MFA-only mode.
                if case.mode == OwnerEmergencyRecoveryCase.Mode.MFA:
                    PasswordResetToken.objects.filter(
                        user=owner, is_used=False,
                    ).update(is_used=True)
                TwoFactorBackupCode.objects.filter(user=owner).delete()
                TwoFactorSetup.objects.filter(user=owner).delete()
                RecoveryLoginChallenge.objects.filter(
                    user=owner, consumed_at__isnull=True
                ).update(consumed_at=timezone.now())
                owner.two_factor_enabled = False
                owner.hub_mfa_reenrollment_required = True
                owner.hub_security_epoch += 1
                fields = ["two_factor_enabled", "hub_mfa_reenrollment_required", "hub_security_epoch"]
                if case.mode == OwnerEmergencyRecoveryCase.Mode.FULL:
                    owner.set_unusable_password()
                    fields.append("password")
                owner.save(update_fields=fields)
                revoked = revoke_all_refresh_tokens(owner)
                # User-wide reset affects all Hub workspaces. Revoke every
                # machine key issued by this person, not only this tenant.
                api_keys = APIKey.objects.filter(
                    created_by=owner, is_active=True,
                ).update(is_active=False)
                case.completed_at = timezone.now()
                case.save(update_fields=["completed_at"])
                record_platform_action(
                    actor=case.approved_by, action="owner.emergency_completed",
                    tenant=tenant, target_user=owner, reason=case.reason,
                    metadata={
                        "case_id": str(case.id), "mode":case.mode,
                        "refresh_tokens_revoked": revoked, "api_keys_revoked": api_keys,
                    },
                )
        except RecoveryDeliveryFailed:
            return Response({"detail": "Password reset message could not be delivered. No credentials were changed."}, status=503)
        for addr in {owner.email, case.recovery_address}:
            send_transactional(
                subject="MateMail: Owner emergency recovery completed",
                body=(
                    "An emergency recovery of your MateMail workspace owner "
                    "account was completed. Existing sessions, authenticator and "
                    "backup codes are invalidated. New authenticator enrollment "
                    "is required after signing in. If unexpected, contact security."
                ),
                to=[addr], purpose="owner_emergency_completed_notice",
            )
        return Response({
            "detail": (
                "Full recovery approved. Check your external recovery email to "
                "set a new password, then enroll a new authenticator."
                if case.mode == OwnerEmergencyRecoveryCase.Mode.FULL else
                "Authenticator reset approved. Sign in with your current password and enroll a new authenticator."
            ),
            "requires_new_enrollment": True,
            "password_reset_required": case.mode == OwnerEmergencyRecoveryCase.Mode.FULL,
        })


class RecoveryDeliveryFailed(Exception):
    pass
