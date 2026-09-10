import hashlib
import logging

import pyotp
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.billing.utils import TRIAL_DAYS
from apps.tenants.models import MemberRole, MemberStatus, Tenant, TenantMembership, TenantStatus
from .models import (
    EmailVerificationToken,
    PasswordResetToken,
    TwoFactorBackupCode,
    TwoFactorSetup,
    User,
)
from .serializers import (
    ForgotPasswordSerializer,
    LoginSerializer,
    ResetPasswordSerializer,
    SignupSerializer,
    TwoFactorDisableSerializer,
    TwoFactorSetupConfirmSerializer,
    TwoFactorVerifySerializer,
    UpdateProfileSerializer,
    UserProfileSerializer,
    VerifyEmailSerializer,
)
from .challenge import (
    challenge_matches_password,
    consume_challenge,
    create_challenge,
    discard_challenge,
    peek_challenge,
    register_failed_attempt,
)
from .tokens import (
    generate_backup_codes,
    hash_token,
    make_tokens,
    revoke_all_refresh_tokens,
)


logger = logging.getLogger(__name__)


class AuthThrottle(AnonRateThrottle):
    scope = "auth"


def _tenant_brief(tenant):
    return {"id": str(tenant.id), "name": tenant.name, "slug": tenant.slug, "status": tenant.status}


class SignupView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if User.objects.filter(email=data["email"]).exists():
            return Response({"email": "An account with this email already exists."}, status=400)

        with transaction.atomic():
            user = User.objects.create_user(
                email=data["email"],
                password=data["password"],
                full_name=data["full_name"],
            )
            slug = _unique_slug(slugify(data["workspace_name"]))
            tenant = Tenant.objects.create(
                name=data["workspace_name"],
                slug=slug,
                owner=user,
                status=TenantStatus.TRIAL,
            )
            TenantMembership.objects.create(
                tenant=tenant,
                user=user,
                role=MemberRole.OWNER,
                status=MemberStatus.ACTIVE,
            )
            # Create 30-day trial subscription
            from apps.billing.models import Plan, PlanTier, Subscription, SubscriptionStatus
            trial_plan = Plan.objects.filter(tier=PlanTier.TRIAL, is_active=True).first()
            if trial_plan:
                Subscription.objects.create(
                    tenant=tenant,
                    plan=trial_plan,
                    status=SubscriptionStatus.TRIALING,
                    trial_ends_at=timezone.now() + timezone.timedelta(days=TRIAL_DAYS),
                )

        _send_verification_email(user)
        tokens = make_tokens(user, tenant_id=tenant.id)

        return Response(
            {**tokens, "user": UserProfileSerializer(user).data, "tenant": _tenant_brief(tenant)},
            status=201,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = authenticate(request, username=data["email"], password=data["password"])
        if user is None:
            return Response({"detail": "Invalid credentials."}, status=401)
        if not user.is_active:
            return Response({"detail": "Account is disabled."}, status=401)

        membership = (
            TenantMembership.objects.select_related("tenant")
            .filter(user=user, status="active")
            .order_by("created_at")
            .first()
        )
        tenant_id = membership.tenant_id if membership else None

        if user.two_factor_enabled:
            # Opaque server-side challenge — deliberately NOT a JWT, so it cannot
            # be replayed as an API access token. See apps.accounts.challenge.
            partial = create_challenge(user, tenant_id)
            return Response({"requires_2fa": True, "partial_token": partial})

        tokens = make_tokens(user, tenant_id=tenant_id)
        return Response(
            {
                **tokens,
                "user": UserProfileSerializer(user).data,
                "tenant": _tenant_brief(membership.tenant) if membership else None,
            }
        )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # A malformed or already-blacklisted token is not an error worth surfacing:
        # the caller's intent (end this session) is satisfied either way. Anything
        # else is a real failure and must not be swallowed.
        try:
            RefreshToken(request.data.get("refresh", "")).blacklist()
        except (InvalidToken, TokenError):
            logger.info("Logout called with an invalid or already-revoked refresh token.")
        return Response({"detail": "Logged out."})


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            user = User.objects.get(email=email, is_active=True)
            raw, _ = PasswordResetToken.make(user)
            reset_url = f"{settings.FRONTEND_URL}/reset-password?token={raw}"
            send_mail(
                subject="Reset your MateMail password",
                message=f"Click this link to reset your password (expires in 1 hour):\n\n{reset_url}",
                from_email=f"MateMail <noreply@{settings.MAIL_DOMAIN}>",
                recipient_list=[email],
                fail_silently=True,
            )
        except User.DoesNotExist:
            pass  # Never reveal whether the email exists

        return Response({"detail": "If that email is registered you will receive a reset link."})


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        token_hash = hash_token(data["token"])
        try:
            token_obj = PasswordResetToken.objects.select_related("user").get(
                token_hash=token_hash,
                is_used=False,
            )
        except PasswordResetToken.DoesNotExist:
            return Response({"detail": "Invalid or expired reset link."}, status=400)

        if timezone.now() > token_obj.expires_at:
            return Response({"detail": "This reset link has expired."}, status=400)

        user = token_obj.user
        user.set_password(data["new_password"])
        user.save(update_fields=["password"])
        token_obj.is_used = True
        token_obj.save(update_fields=["is_used"])

        # A password reset is a security event: assume the old credential is
        # compromised and cut every session that could outlive it.
        revoked = revoke_all_refresh_tokens(user)

        # Burn any other outstanding reset links for this user, so a second
        # emailed link cannot be replayed later.
        also_used = PasswordResetToken.objects.filter(
            user=user, is_used=False
        ).exclude(pk=token_obj.pk).update(is_used=True)

        # Any in-flight 2FA challenge is bound to the old password hash and is
        # therefore already void — see challenge_matches_password().

        logger.info(
            "Password reset completed for user %s — revoked %d refresh token(s), "
            "invalidated %d other reset token(s)",
            user.pk, revoked, also_used,
        )

        return Response({"detail": "Password updated. You can now log in."})


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token_hash = hash_token(serializer.validated_data["token"])
        try:
            token_obj = EmailVerificationToken.objects.select_related("user").get(
                token_hash=token_hash,
                is_used=False,
            )
        except EmailVerificationToken.DoesNotExist:
            return Response({"detail": "Invalid or expired verification link."}, status=400)

        if timezone.now() > token_obj.expires_at:
            return Response({"detail": "This verification link has expired."}, status=400)

        token_obj.user.email_verified = True
        token_obj.user.save(update_fields=["email_verified"])
        token_obj.is_used = True
        token_obj.save(update_fields=["is_used"])

        return Response({"detail": "Email verified successfully."})


class ResendVerificationView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        if request.user.email_verified:
            return Response({"detail": "Email is already verified."})
        _send_verification_email(request.user)
        return Response({"detail": "Verification email sent."})


class TwoFactorSetupView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        try:
            setup = user.two_factor_setup
            secret = setup.totp_secret
        except TwoFactorSetup.DoesNotExist:
            secret = pyotp.random_base32()
            TwoFactorSetup.objects.create(user=user, totp_secret=secret)

        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=user.email,
            issuer_name="MateMail",
        )
        return Response({"secret": secret, "uri": uri})

    def post(self, request):
        serializer = TwoFactorSetupConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"]

        user = request.user
        try:
            setup = user.two_factor_setup
        except TwoFactorSetup.DoesNotExist:
            return Response({"detail": "2FA setup not initiated. Call GET first."}, status=400)

        totp = pyotp.TOTP(setup.totp_secret)
        if not totp.verify(code, valid_window=1):
            return Response({"detail": "Invalid code."}, status=400)

        # Enable 2FA and generate backup codes
        raw_codes = generate_backup_codes(10)
        TwoFactorBackupCode.objects.filter(user=user).delete()
        TwoFactorBackupCode.objects.bulk_create([
            TwoFactorBackupCode(
                user=user,
                code_hash=hash_token(c),
            )
            for c in raw_codes
        ])
        user.two_factor_enabled = True
        user.save(update_fields=["two_factor_enabled"])

        return Response({"detail": "2FA enabled.", "backup_codes": raw_codes})


class TwoFactorVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        serializer = TwoFactorVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        raw_challenge = data["partial_token"]
        payload = peek_challenge(raw_challenge)
        if payload is None:
            return Response({"detail": "Invalid or expired token."}, status=401)

        try:
            user = User.objects.get(id=payload["user_id"], is_active=True)
        except (User.DoesNotExist, KeyError, ValidationError):
            return Response({"detail": "User not found."}, status=401)

        if not challenge_matches_password(payload, user):
            # Password changed after this challenge was issued (e.g. a reset).
            discard_challenge(raw_challenge)
            return Response({"detail": "Invalid or expired token."}, status=401)

        code = data["code"]
        try:
            setup = user.two_factor_setup
        except TwoFactorSetup.DoesNotExist:
            return Response({"detail": "2FA not configured."}, status=401)

        totp = pyotp.TOTP(setup.totp_secret)
        backup = None
        if not totp.verify(code, valid_window=1):
            # Try backup code
            code_hash = hash_token(code)
            backup = TwoFactorBackupCode.objects.filter(
                user=user, code_hash=code_hash, is_used=False
            ).first()
            if not backup:
                register_failed_attempt(raw_challenge)
                return Response({"detail": "Invalid authentication code."}, status=401)

        # Claim the challenge before issuing credentials. delete() returns False if
        # a concurrent request already claimed it, which keeps the token single-use.
        if not consume_challenge(raw_challenge):
            return Response({"detail": "Invalid or expired token."}, status=401)

        if backup is not None:
            backup.is_used = True
            backup.save(update_fields=["is_used"])

        tenant_id = payload.get("tenant_id")
        tokens = make_tokens(user, tenant_id=tenant_id)
        membership = (
            TenantMembership.objects.select_related("tenant")
            .filter(tenant_id=tenant_id, user=user, status="active")
            .first()
        ) if tenant_id else None

        return Response(
            {
                **tokens,
                "user": UserProfileSerializer(user).data,
                "tenant": _tenant_brief(membership.tenant) if membership else None,
            }
        )


class TwoFactorDisableView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TwoFactorDisableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data["password"]):
            return Response({"detail": "Incorrect password."}, status=400)

        TwoFactorBackupCode.objects.filter(user=user).delete()
        TwoFactorSetup.objects.filter(user=user).delete()
        user.two_factor_enabled = False
        user.save(update_fields=["two_factor_enabled"])

        return Response({"detail": "2FA disabled."})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserProfileSerializer(request.user).data)

    def patch(self, request):
        serializer = UpdateProfileSerializer(
            request.user, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserProfileSerializer(request.user).data)


# ── helpers ──────────────────────────────────────────────────────────────────

def _unique_slug(base: str) -> str:
    slug = base or "workspace"
    counter = 1
    while Tenant.objects.filter(slug=slug).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _send_verification_email(user):
    raw, _ = EmailVerificationToken.make(user)
    verify_url = f"{settings.FRONTEND_URL}/verify-email?token={raw}"
    send_mail(
        subject="Verify your MateMail email address",
        message=(
            f"Welcome to MateMail!\n\n"
            f"Click this link to verify your email address (expires in 24 hours):\n\n"
            f"{verify_url}"
        ),
        from_email=f"MateMail <noreply@{settings.MAIL_DOMAIN}>",
        recipient_list=[user.email],
        fail_silently=True,
    )
