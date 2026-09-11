import hashlib
import logging

import pyotp
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
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
from apps.security import ratelimit
from apps.security.client_ip import get_client_ip
from apps.security.limits import (
    FORGOT_PASSWORD_PER_EMAIL,
    LOGIN_PER_ACCOUNT,
    LOGIN_PER_IP,
    SIGNUP_PER_IP,
    TOTP_REPLAY_TTL,
    TWO_FACTOR_MANAGE_PER_USER,
    TWO_FACTOR_PER_USER,
)
from apps.security.throttling import AuthenticatedActionThrottle, AuthEndpointThrottle
from .mailer import send_transactional
from .cookies import (
    clear_refresh_cookie,
    read_refresh_cookie,
    set_refresh_cookie,
)


logger = logging.getLogger(__name__)


#: Per-IP volume cap on the unauthenticated auth endpoints. The named limits
#: in apps.security.limits are the real controls; this is the coarse brake in
#: front of them.
AuthThrottle = AuthEndpointThrottle

#: One message for every refused sign-in, whatever the reason. A caller must
#: not be able to tell "wrong password" from "this account is locked" from "no
#: such account" — each distinction is an oracle.
_GENERIC_LOGIN_FAILURE = "Invalid credentials."
_TOO_MANY_ATTEMPTS = (
    "Too many sign-in attempts. Please wait a few minutes and try again."
)


def _rate_limit_email_key(value) -> str:
    """
    Case-insensitive key for per-account limits.

    Used ONLY as a bucket identity. It is deliberately not what gets passed to
    authenticate() or to a database lookup: UserManager.normalize_email
    lowercases the domain and leaves the local part alone, so folding the whole
    address here and using it for lookups would lock out every account with a
    capital letter before the @.
    """
    return (value or "").strip().lower()


def _enforce(decision, detail):
    """Raise DRF's Throttled — which sets Retry-After — when over the limit."""
    if not decision.allowed:
        raise Throttled(wait=decision.retry_after, detail=detail)


def _tenant_brief(tenant):
    return {"id": str(tenant.id), "name": tenant.name, "slug": tenant.slug, "status": tenant.status}


def authenticated_response(tokens: dict, payload: dict, *, status: int = 200):
    """
    Return the access token in the body and the refresh token in a cookie.

    The refresh token is deliberately absent from `payload`: leaving it in the
    body as well would put it straight back into any script's reach, and the
    weaker of two mechanisms is the one that defines the security of the pair.
    """
    response = Response({"access": tokens["access"], **payload}, status=status)
    return set_refresh_cookie(response, tokens["refresh"])


class SignupView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        # Counted before validation: a signup flood does not become cheaper by
        # being malformed, and the account this would create is the resource
        # being protected.
        _enforce(
            ratelimit.hit(
                SIGNUP_PER_IP.bucket,
                get_client_ip(request),
                limit=SIGNUP_PER_IP.limit,
                window=SIGNUP_PER_IP.window,
            ),
            "Too many workspaces created from this address. Please try again later.",
        )

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

        return authenticated_response(
            tokens,
            {
                "user": UserProfileSerializer(user).data,
                "tenant": _tenant_brief(tenant),
            },
            status=201,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = data["email"]
        account_key = _rate_limit_email_key(email)
        client_ip = get_client_ip(request)

        # Read the counters before spending an attempt. Failures are what get
        # counted (see below), so someone who signs in correctly never
        # accumulates a lockout, and one NAT gateway cannot lock out an office.
        for decision in (
            ratelimit.check(
                LOGIN_PER_IP.bucket, client_ip,
                limit=LOGIN_PER_IP.limit, window=LOGIN_PER_IP.window,
            ),
            ratelimit.check(
                LOGIN_PER_ACCOUNT.bucket, account_key,
                limit=LOGIN_PER_ACCOUNT.limit, window=LOGIN_PER_ACCOUNT.window,
            ),
        ):
            _enforce(decision, _TOO_MANY_ATTEMPTS)

        user = authenticate(request, username=email, password=data["password"])
        if user is None or not user.is_active:
            # Both branches count and both answer identically. An inactive
            # account previously returned a distinct message, which told an
            # attacker the address was registered.
            ratelimit.hit(
                LOGIN_PER_IP.bucket, client_ip,
                limit=LOGIN_PER_IP.limit, window=LOGIN_PER_IP.window,
            )
            ratelimit.hit(
                LOGIN_PER_ACCOUNT.bucket, account_key,
                limit=LOGIN_PER_ACCOUNT.limit, window=LOGIN_PER_ACCOUNT.window,
            )
            return Response({"detail": _GENERIC_LOGIN_FAILURE}, status=401)

        # The password was right. Clear the counters even when a second factor
        # is still outstanding — the credential under brute-force attack here
        # is the password, and it has just been presented correctly.
        ratelimit.reset(LOGIN_PER_IP.bucket, client_ip, window=LOGIN_PER_IP.window)
        ratelimit.reset(LOGIN_PER_ACCOUNT.bucket, account_key, window=LOGIN_PER_ACCOUNT.window)

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
        return authenticated_response(
            tokens,
            {
                "user": UserProfileSerializer(user).data,
                "tenant": _tenant_brief(membership.tenant) if membership else None,
            },
        )


class RefreshView(APIView):
    """
    POST /api/auth/refresh/ — exchange the refresh cookie for a new access token.

    Replaces simplejwt's `TokenRefreshView`, which reads the token from the
    request body. Reading it from the body would mean JavaScript still had to
    hold it, which is the exposure this endpoint exists to remove.

    Rotation is on (`ROTATE_REFRESH_TOKENS`), so the response also replaces the
    cookie. A refresh that fails clears the cookie rather than leaving the
    browser to retry a credential the server has already rejected.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        raw = read_refresh_cookie(request)
        if not raw:
            return Response({"detail": "Not authenticated."}, status=401)

        try:
            token = RefreshToken(raw)
            access = str(token.access_token)
            # BLACKLIST_AFTER_ROTATION is on: blacklisting must happen before a
            # replacement is issued, so a stolen token cannot be exchanged twice.
            token.blacklist()
            new_refresh = RefreshToken.for_user(_user_for(token))
            if token.get("tenant_id"):
                new_refresh["tenant_id"] = token["tenant_id"]
                access = str(new_refresh.access_token)
        except (InvalidToken, TokenError, User.DoesNotExist):
            # Expired, blacklisted, tampered with, or the account is gone.
            return clear_refresh_cookie(
                Response({"detail": "Session expired. Please sign in again."}, status=401)
            )

        return authenticated_response({"access": access, "refresh": str(new_refresh)}, {})


def _user_for(token):
    """The account a refresh token belongs to, or raise User.DoesNotExist."""
    from rest_framework_simplejwt.settings import api_settings

    return User.objects.get(
        **{api_settings.USER_ID_FIELD: token[api_settings.USER_ID_CLAIM]},
        is_active=True,
    )


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # A malformed or already-blacklisted token is not an error worth surfacing:
        # the caller's intent (end this session) is satisfied either way. Anything
        # else is a real failure and must not be swallowed.
        try:
            RefreshToken(read_refresh_cookie(request)).blacklist()
        except (InvalidToken, TokenError):
            logger.info("Logout called with an invalid or already-revoked refresh token.")
        # The cookie goes whether or not the token was still valid. Leaving it
        # in place would keep a credential in the browser that the user has
        # explicitly asked to be rid of.
        return clear_refresh_cookie(Response({"detail": "Logged out."}))


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        # Counted for every request, whether or not the address is registered.
        # A limit that only applied to real accounts would answer 429 for those
        # and 200 for the rest — the existence oracle this endpoint's uniform
        # response exists to avoid.
        _enforce(
            ratelimit.hit(
                FORGOT_PASSWORD_PER_EMAIL.bucket,
                _rate_limit_email_key(email),
                limit=FORGOT_PASSWORD_PER_EMAIL.limit,
                window=FORGOT_PASSWORD_PER_EMAIL.window,
            ),
            "Too many reset requests for that address. Please try again later.",
        )

        try:
            user = User.objects.get(email=email, is_active=True)
        except User.DoesNotExist:
            user = None

        if user is not None:
            raw, _ = PasswordResetToken.make(user)
            reset_url = f"{settings.FRONTEND_URL}/reset-password?token={raw}"
            send_transactional(
                subject="Reset your MateMail password",
                body=(
                    f"Someone asked to reset the password for your MateMail "
                    f"account.\n\n"
                    f"Use this link within the next hour:\n\n{reset_url}\n\n"
                    f"If it wasn't you, no action is needed — your password has "
                    f"not changed.\n\n"
                    f"— MateMail, by NetaMate Solutions"
                ),
                to=email,
                purpose="password-reset",
            )
        # The response below is identical either way. A failure to send is
        # logged by the mailer; it must not change what the caller is told,
        # because a different answer for a real address is an existence oracle.

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

        # Every refresh token was just blacklisted; drop this browser's copy so
        # it does not sit there being rejected.
        return clear_refresh_cookie(
            Response({"detail": "Password updated. You can now log in."})
        )


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
    # AuthThrottle is an AnonRateThrottle: it returns immediately for an
    # authenticated request, so this endpoint used to have no limit at all.
    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthenticatedActionThrottle]

    def post(self, request):
        if request.user.email_verified:
            return Response({"detail": "Email is already verified."})

        # Unlike the password-reset endpoint there is no existence oracle here
        # — the caller is authenticated and already knows the address — so the
        # honest answer is available and is given. Saying "sent" when nothing
        # was sent leaves the customer waiting at a wall they cannot pass.
        if not _send_verification_email(request.user):
            return Response(
                {
                    "detail": (
                        "We could not send the verification email just now. "
                        "Please try again in a few minutes."
                    )
                },
                status=503,
            )
        return Response({"detail": "Verification email sent."})


class TwoFactorSetupView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthenticatedActionThrottle]

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

        # Enrolment confirms possession of the secret, so wrong codes are
        # counted the same way a failed login is: without this, a signed-in
        # session could grind the six-digit space against a known secret.
        _enforce(
            ratelimit.check(
                TWO_FACTOR_MANAGE_PER_USER.bucket, user.pk,
                limit=TWO_FACTOR_MANAGE_PER_USER.limit,
                window=TWO_FACTOR_MANAGE_PER_USER.window,
            ),
            _TOO_MANY_ATTEMPTS,
        )

        totp = pyotp.TOTP(setup.totp_secret)
        if not totp.verify(code, valid_window=1):
            ratelimit.hit(
                TWO_FACTOR_MANAGE_PER_USER.bucket, user.pk,
                limit=TWO_FACTOR_MANAGE_PER_USER.limit,
                window=TWO_FACTOR_MANAGE_PER_USER.window,
            )
            return Response({"detail": "Invalid code."}, status=400)

        ratelimit.reset(
            TWO_FACTOR_MANAGE_PER_USER.bucket, user.pk,
            window=TWO_FACTOR_MANAGE_PER_USER.window,
        )

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

        # The per-challenge counter in apps.accounts.challenge caps attempts
        # against one token. It does not stop an attacker who holds the
        # password: after five failures they simply log in again for a fresh
        # challenge. This limit follows the user, not the token.
        _enforce(
            ratelimit.check(
                TWO_FACTOR_PER_USER.bucket, user.pk,
                limit=TWO_FACTOR_PER_USER.limit, window=TWO_FACTOR_PER_USER.window,
            ),
            _TOO_MANY_ATTEMPTS,
        )

        code = data["code"]
        try:
            setup = user.two_factor_setup
        except TwoFactorSetup.DoesNotExist:
            return Response({"detail": "2FA not configured."}, status=401)

        def _count_failure():
            register_failed_attempt(raw_challenge)
            ratelimit.hit(
                TWO_FACTOR_PER_USER.bucket, user.pk,
                limit=TWO_FACTOR_PER_USER.limit, window=TWO_FACTOR_PER_USER.window,
            )

        totp = pyotp.TOTP(setup.totp_secret)
        backup = None
        if totp.verify(code, valid_window=1):
            # A TOTP code stays valid for its whole timestep — with
            # valid_window=1, for ninety seconds. Anyone who observes one
            # (a phishing relay, a shoulder surf, a screen share) can present
            # it again inside that window against a challenge of their own.
            # Accepting a code claims it for that user until it expires.
            if not ratelimit.claim_once(
                "2fa:totp", f"{user.pk}:{code}", ttl=TOTP_REPLAY_TTL
            ):
                logger.warning("Replayed TOTP code rejected for user %s", user.pk)
                _count_failure()
                return Response({"detail": "Invalid authentication code."}, status=401)
        else:
            # Try backup code
            code_hash = hash_token(code)
            backup = TwoFactorBackupCode.objects.filter(
                user=user, code_hash=code_hash, is_used=False
            ).first()
            if not backup:
                _count_failure()
                return Response({"detail": "Invalid authentication code."}, status=401)

        # Claim the challenge before issuing credentials. delete() returns False if
        # a concurrent request already claimed it, which keeps the token single-use.
        if not consume_challenge(raw_challenge):
            return Response({"detail": "Invalid or expired token."}, status=401)

        if backup is not None:
            backup.is_used = True
            backup.save(update_fields=["is_used"])

        ratelimit.reset(
            TWO_FACTOR_PER_USER.bucket, user.pk, window=TWO_FACTOR_PER_USER.window
        )

        tenant_id = payload.get("tenant_id")
        tokens = make_tokens(user, tenant_id=tenant_id)
        membership = (
            TenantMembership.objects.select_related("tenant")
            .filter(tenant_id=tenant_id, user=user, status="active")
            .first()
        ) if tenant_id else None

        return authenticated_response(
            tokens,
            {
                "user": UserProfileSerializer(user).data,
                "tenant": _tenant_brief(membership.tenant) if membership else None,
            },
        )


class TwoFactorDisableView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [AuthenticatedActionThrottle]

    def post(self, request):
        serializer = TwoFactorDisableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        # Turning off the second factor takes the account password. A stolen
        # access token must not be able to guess it without a brake.
        _enforce(
            ratelimit.check(
                TWO_FACTOR_MANAGE_PER_USER.bucket, user.pk,
                limit=TWO_FACTOR_MANAGE_PER_USER.limit,
                window=TWO_FACTOR_MANAGE_PER_USER.window,
            ),
            _TOO_MANY_ATTEMPTS,
        )
        if not user.check_password(serializer.validated_data["password"]):
            ratelimit.hit(
                TWO_FACTOR_MANAGE_PER_USER.bucket, user.pk,
                limit=TWO_FACTOR_MANAGE_PER_USER.limit,
                window=TWO_FACTOR_MANAGE_PER_USER.window,
            )
            return Response({"detail": "Incorrect password."}, status=400)

        ratelimit.reset(
            TWO_FACTOR_MANAGE_PER_USER.bucket, user.pk,
            window=TWO_FACTOR_MANAGE_PER_USER.window,
        )

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


def _send_verification_email(user) -> bool:
    """Send the address-verification link. Returns whether it was accepted."""
    raw, _ = EmailVerificationToken.make(user)
    verify_url = f"{settings.FRONTEND_URL}/verify-email?token={raw}"
    return send_transactional(
        subject="Verify your MateMail email address",
        body=(
            f"Welcome to MateMail.\n\n"
            f"Confirm this address to finish setting up your workspace. The "
            f"link is good for 24 hours:\n\n{verify_url}\n\n"
            f"If you did not create a MateMail account, you can ignore this "
            f"message.\n\n"
            f"— MateMail, by NetaMate Solutions"
        ),
        to=user.email,
        purpose="email-verification",
    )
