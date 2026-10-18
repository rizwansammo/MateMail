"""Verified secondary recovery address and fallback OTP for Hub MFA.

Recovery never uses the primary MateMail mailbox as its factor. The address
must be distinct, verified by an independent one-time email challenge, and
protected by current password plus an enrolled second factor on enrollment.
Codes are challenge-bound and one-time; no plaintext OTP is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

import pyotp
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.security import ratelimit
from apps.security.limits import TWO_FACTOR_MANAGE_PER_USER, TWO_FACTOR_PER_USER
from apps.security.throttling import AuthEndpointThrottle, AuthenticatedActionThrottle
from apps.tenants.host_binding import bound_tenant_slug, scope_memberships
from apps.tenants.models import MemberStatus, TenantMembership
from .challenge import (
    peek_challenge, challenge_matches_password, consume_challenge, discard_challenge
)
from .mailer import send_transactional
from .models import User, VerifiedRecoveryEmail, RecoveryLoginChallenge
from .tokens import make_tokens
from .totp_crypto import decrypt_secret
from .views import authenticated_response, _tenant_brief
from .serializers import UserProfileSerializer


def _digest(token, code=None):
    source = token if code is None else token + ":" + code
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _otp():
    return f"{secrets.randbelow(1_000_000):06d}"


def _throttle(user_id, *, manage=False):
    limit = TWO_FACTOR_MANAGE_PER_USER if manage else TWO_FACTOR_PER_USER
    result = ratelimit.hit(
        limit.bucket, str(user_id), limit=limit.limit, window=limit.window,
    )
    return result.allowed


class SetupInput(serializers.Serializer):
    recovery_email = serializers.EmailField()
    current_password = serializers.CharField(trim_whitespace=False)
    totp_code = serializers.CharField(required=False, default="")


class RecoveryEmailSetupView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthenticatedActionThrottle]

    def get(self, request):
        obj = VerifiedRecoveryEmail.objects.filter(user=request.user).first()
        return Response({
            "verified_email": obj.email if obj and obj.verified_at else None,
            "verified": bool(obj and obj.verified_at),
            "pending": bool(obj and obj.pending_email),
        })

    def post(self, request):
        serializer = SetupInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not _throttle(user.pk, manage=True):
            return Response({"detail": "Too many attempts."}, status=429)
        email = serializer.validated_data["recovery_email"].strip().lower()
        if email == user.email.strip().lower():
            return Response({"detail": "Use an independent recovery email."}, status=400)
        if not user.check_password(serializer.validated_data["current_password"]):
            return Response({"detail": "Reauthentication failed."}, status=403)
        if user.two_factor_enabled:
            from .models import TwoFactorSetup
            setup = TwoFactorSetup.objects.filter(user=user).first()
            code = serializer.validated_data.get("totp_code", "")
            if not setup or not code or not pyotp.TOTP(
                decrypt_secret(setup.totp_secret)
            ).verify(code, valid_window=1):
                return Response({"detail": "Current authenticator proof required."}, status=403)
            if not ratelimit.claim_once(
                "2fa:totp", f"{user.pk}:{code}", ttl=120,
            ):
                return Response({"detail": "Authentication code already used."}, status=403)

        token, code = secrets.token_urlsafe(32), _otp()
        obj, _ = VerifiedRecoveryEmail.objects.get_or_create(user=user)
        # Never replace a verified recovery address before the new one proves
        # ownership, including on transactional-email failures.
        obj.pending_email = email
        obj.challenge_hash = _digest(token)
        obj.code_hash = _digest(token, code)
        obj.expires_at = timezone.now() + timedelta(minutes=10)
        obj.attempts = 0
        obj.save(update_fields=[
            "pending_email", "challenge_hash", "code_hash", "expires_at", "attempts",
        ])
        if not send_transactional(
            subject="Verify your MateMail recovery email",
            body=f"Your independent recovery email verification code is {code}. It expires in 10 minutes.",
            to=[email], purpose="recovery_email_verification",
        ):
            VerifiedRecoveryEmail.objects.filter(
                user=user, challenge_hash=_digest(token),
            ).update(pending_email="", challenge_hash="", code_hash="", expires_at=None)
            return Response({"detail": "Could not send recovery verification."}, status=503)
        return Response({"challenge": token, "detail": "Verification code sent."})


class ConfirmationInput(serializers.Serializer):
    challenge = serializers.CharField(min_length=32, max_length=128)
    code = serializers.RegexField(regex=r"^[0-9]{6}$")


class RecoveryEmailConfirmView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthenticatedActionThrottle]

    def post(self, request):
        serializer = ConfirmationInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        if not _throttle(request.user.pk, manage=True):
            return Response({"detail": "Too many attempts."}, status=429)
        with transaction.atomic():
            obj = VerifiedRecoveryEmail.objects.select_for_update().filter(user=request.user).first()
            if (obj is None or not obj.pending_email or not obj.expires_at
                    or obj.expires_at <= timezone.now() or obj.attempts >= 5
                    or not hmac.compare_digest(obj.challenge_hash,
                                               _digest(payload["challenge"]))):
                return Response({"detail": "Verification failed."}, status=401)
            if not hmac.compare_digest(obj.code_hash,
                                       _digest(payload["challenge"], payload["code"])):
                obj.attempts += 1
                obj.save(update_fields=["attempts"])
                return Response({"detail": "Verification failed."}, status=401)
            obj.email = obj.pending_email
            obj.verified_at = timezone.now()
            obj.pending_email = ""
            obj.challenge_hash = ""
            obj.code_hash = ""
            obj.expires_at = None
            obj.attempts = 0
            obj.save()
        return Response({"verified": True})


class RecoveryStartInput(serializers.Serializer):
    partial_token = serializers.CharField(min_length=32, max_length=256)


class RecoveryLoginStartView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthEndpointThrottle]

    def post(self, request):
        serializer = RecoveryStartInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data["partial_token"]
        challenge = peek_challenge(raw)
        if challenge is None:
            return Response({"detail": "Invalid challenge."}, status=401)
        user = User.objects.filter(pk=challenge.get("user_id"), is_active=True).first()
        if not user or not challenge_matches_password(challenge, user) or not user.two_factor_enabled:
            return Response({"detail": "Invalid challenge."}, status=401)
        if not _throttle(user.pk):
            return Response({"detail": "Too many attempts."}, status=429)
        contact = VerifiedRecoveryEmail.objects.filter(
            user=user, verified_at__isnull=False,
        ).first()
        if not contact or not contact.email or contact.email.lower() == user.email.lower():
            return Response({"detail": "Verified independent recovery unavailable."}, status=409)
        code = _otp()
        nonce = secrets.token_urlsafe(32)
        # The original password-proven partial token remains the authority.
        row = RecoveryLoginChallenge.objects.create(
            user=user,
            challenge_hash=_digest(nonce),
            code_hash=_digest(nonce, code),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        if not send_transactional(
            subject="MateMail account recovery verification",
            body=f"Your MateMail recovery authentication code is {code}. It expires in 10 minutes.",
            to=[contact.email], purpose="2fa_recovery_email",
        ):
            row.delete()
            return Response({"detail": "Recovery code could not be sent."}, status=503)
        return Response({"recovery_challenge": nonce, "detail": "Recovery code sent."})


class RecoveryFinishInput(RecoveryStartInput):
    recovery_challenge = serializers.CharField(min_length=32, max_length=128)
    code = serializers.RegexField(regex=r"^[0-9]{6}$")


class RecoveryLoginFinishView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [AuthEndpointThrottle]

    def post(self, request):
        serializer = RecoveryFinishInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        raw = payload["partial_token"]
        challenge = peek_challenge(raw)
        if not challenge:
            return Response({"detail": "Invalid challenge."}, status=401)
        user = User.objects.filter(pk=challenge.get("user_id"), is_active=True).first()
        if not user or not user.two_factor_enabled or not challenge_matches_password(challenge, user):
            return Response({"detail": "Invalid challenge."}, status=401)
        if not _throttle(user.pk):
            return Response({"detail": "Too many attempts."}, status=429)
        tenant_id = challenge.get("tenant_id")
        membership = scope_memberships(
            request, TenantMembership.objects.select_related("tenant").filter(
                user=user, tenant_id=tenant_id, status=MemberStatus.ACTIVE,
            ),
        ).first() if tenant_id else None
        if (tenant_id and membership is None) or (bound_tenant_slug(request) and membership is None):
            # A valid email code must not be consumed before verifying that
            # this caller is still allowed on the current organization/host.
            return Response({"detail": "Account not available on this host."}, status=403)
        with transaction.atomic():
            row = RecoveryLoginChallenge.objects.select_for_update().filter(
                user=user,
                challenge_hash=_digest(payload["recovery_challenge"]),
                consumed_at__isnull=True,
            ).first()
            if not row or row.expires_at <= timezone.now() or row.attempts >= 5:
                return Response({"detail": "Invalid recovery code."}, status=401)
            if not hmac.compare_digest(
                row.code_hash, _digest(payload["recovery_challenge"], payload["code"])
            ):
                row.attempts += 1
                row.save(update_fields=["attempts"])
                return Response({"detail": "Invalid recovery code."}, status=401)
            if not consume_challenge(raw):
                return Response({"detail": "Invalid challenge."}, status=401)
            row.consumed_at = timezone.now()
            row.save(update_fields=["consumed_at"])
        tokens = make_tokens(user, tenant_id=tenant_id, mfa_verified=True)
        return authenticated_response(tokens, {
            "user": UserProfileSerializer(user).data,
            "tenant": _tenant_brief(membership.tenant) if membership else None,
            "recovery_used": True,
        })
