"""
Platform Console authentication endpoints.

    POST /api/platform/auth/login/            email + password  -> challenge
    POST /api/platform/auth/verify/           challenge + code  -> session
    POST /api/platform/auth/resend/           challenge         -> new code
    POST /api/platform/auth/forgot-password/  email             -> challenge
    POST /api/platform/auth/reset-password/   challenge + code + password

No endpoint here issues an access token or a refresh cookie except
`PlatformVerifyView`, and that one runs only after `verify_code` has returned a
user. See apps.platform_admin.auth for why the intermediate token is opaque.
"""
from __future__ import annotations

import logging

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.cookies import clear_refresh_cookie
from apps.accounts.models import PlatformCodePurpose, PlatformEmailCode
from apps.accounts.tokens import make_tokens, revoke_all_refresh_tokens
from apps.accounts.views import authenticated_response
from apps.security import ratelimit
from apps.security.client_ip import get_client_ip
from apps.security.limits import (
    PLATFORM_CODE_PER_ACCOUNT,
    PLATFORM_CODE_RESEND,
    PLATFORM_LOGIN_PER_ACCOUNT,
    PLATFORM_LOGIN_PER_IP,
    PLATFORM_RESET_PER_EMAIL,
)

from .auth import (
    GENERIC_CODE_FAILURE,
    GENERIC_FAILURE,
    CodeDelivery,
    eligible_platform_admin,
    invalidate_outstanding,
    issue_and_send,
    verify_code,
)

logger = logging.getLogger(__name__)
User = get_user_model()

_TOO_MANY = "Too many attempts. Please wait and try again."

#: Returned when a code was issued but the mail could not be handed to the
#: relay. Distinguishable from a credential failure on purpose: this one is our
#: fault, the operator can do nothing about it, and silently showing the code
#: entry screen would leave them typing into a form no code will ever arrive
#: for. It reveals nothing — the caller already proved the password.
_DELIVERY_FAILED = (
    "Your credentials were accepted, but the security code could not be sent. "
    "Contact the platform operator — transactional email may be misconfigured."
)


def _enforce(decision, detail=_TOO_MANY):
    if not decision.allowed:
        raise Throttled(wait=decision.retry_after, detail=detail)


def _email_key(value: str) -> str:
    return (value or "").strip().lower()


# ── serializers ─────────────────────────────────────────────────────────────

class PlatformLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)


class PlatformVerifySerializer(serializers.Serializer):
    challenge = serializers.CharField()
    # Exactly six digits. Bounded here so a megabyte of "code" never reaches
    # the hashing step.
    code = serializers.RegexField(r"^\d{6}$", min_length=6, max_length=6)


class PlatformResendSerializer(serializers.Serializer):
    challenge = serializers.CharField()


class PlatformForgotSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PlatformResetSerializer(serializers.Serializer):
    challenge = serializers.CharField()
    code = serializers.RegexField(r"^\d{6}$", min_length=6, max_length=6)
    new_password = serializers.CharField(trim_whitespace=False)


# ── views ───────────────────────────────────────────────────────────────────

class PlatformLoginView(APIView):
    """
    Stage one: password. Never issues a session.

    A correct password here buys exactly one thing — an opaque challenge and an
    email. That is the whole point of the endpoint: there is no branch, for any
    account, in which a password alone produces credentials.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PlatformLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]

        client_ip = get_client_ip(request)
        account_key = _email_key(email)

        for decision in (
            ratelimit.check(
                PLATFORM_LOGIN_PER_IP.bucket, client_ip,
                limit=PLATFORM_LOGIN_PER_IP.limit, window=PLATFORM_LOGIN_PER_IP.window,
            ),
            ratelimit.check(
                PLATFORM_LOGIN_PER_ACCOUNT.bucket, account_key,
                limit=PLATFORM_LOGIN_PER_ACCOUNT.limit,
                window=PLATFORM_LOGIN_PER_ACCOUNT.window,
            ),
        ):
            _enforce(decision)

        user = eligible_platform_admin(email)
        # `authenticate` is called even when the address has no platform
        # account, so a request for an unknown address costs the same password
        # hashing as a request for a real one. Without it the response time
        # tells an attacker which addresses belong to platform administrators.
        authenticated = authenticate(request, username=email, password=password)

        if user is None or authenticated is None or authenticated.pk != user.pk:
            for rule in (PLATFORM_LOGIN_PER_IP, PLATFORM_LOGIN_PER_ACCOUNT):
                identity = client_ip if rule is PLATFORM_LOGIN_PER_IP else account_key
                ratelimit.hit(rule.bucket, identity, limit=rule.limit, window=rule.window)
            logger.warning("Platform login refused for %s from %s.", account_key, client_ip)
            return Response({"detail": GENERIC_FAILURE}, status=401)

        # The password was correct, so the credential under brute force has
        # been presented successfully. Counters clear even though the login is
        # not finished — the code stage has its own limits.
        ratelimit.reset(PLATFORM_LOGIN_PER_IP.bucket, client_ip,
                        window=PLATFORM_LOGIN_PER_IP.window)
        ratelimit.reset(PLATFORM_LOGIN_PER_ACCOUNT.bucket, account_key,
                        window=PLATFORM_LOGIN_PER_ACCOUNT.window)

        _enforce(ratelimit.hit(
            PLATFORM_CODE_PER_ACCOUNT.bucket, str(user.pk),
            limit=PLATFORM_CODE_PER_ACCOUNT.limit,
            window=PLATFORM_CODE_PER_ACCOUNT.window,
        ))

        challenge, delivery = issue_and_send(
            user, PlatformCodePurpose.LOGIN, request=request
        )
        if delivery != CodeDelivery.SENT:
            return Response({"detail": _DELIVERY_FAILED}, status=503)

        return Response({
            "requires_code": True,
            "challenge": challenge,
            "expires_in": int(PlatformEmailCode.TTL.total_seconds()),
            # So the console can say "sent to r…@gmail.com" without printing an
            # address that a shoulder-surfer could harvest.
            "sent_to": _mask(user.email),
        })


def _mask(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    if not domain:
        return ""
    head = local[:1]
    return f"{head}{'•' * max(len(local) - 1, 1)}@{domain}"


class PlatformVerifyView(APIView):
    """Stage two: the emailed code. The only endpoint here that issues a session."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PlatformVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user, reason = verify_code(
            serializer.validated_data["challenge"],
            serializer.validated_data["code"],
            PlatformCodePurpose.LOGIN,
        )
        if user is None:
            logger.warning(
                "Platform code verification failed (%s) from %s.",
                reason, get_client_ip(request),
            )
            return Response({"detail": GENERIC_CODE_FAILURE}, status=401)

        logger.info("Platform session issued for %s.", user.pk)
        _audit_platform_security(request, user, "platform.login", actor=user)

        # tenant_id is deliberately omitted. A platform administrator's session
        # is not scoped to an organization, and stamping one in would make the
        # token look like a member of whichever workspace they happened to
        # belong to.
        tokens = make_tokens(user, tenant_id=None)
        from apps.accounts.serializers import UserProfileSerializer

        return authenticated_response(tokens, {
            "user": UserProfileSerializer(user).data,
            "platform": True,
        })


class PlatformResendView(APIView):
    """
    A fresh code for an outstanding challenge.

    Returns a NEW challenge: `PlatformEmailCode.issue` consumes the previous
    one, so continuing to accept the old token would mean an attacker could
    keep a challenge alive by asking for resends while guessing.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PlatformResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data["challenge"]

        row = (
            PlatformEmailCode.objects.select_related("user")
            .filter(
                challenge_hash=PlatformEmailCode.hash_challenge(raw),
                purpose=PlatformCodePurpose.LOGIN,
                consumed_at__isnull=True,
            )
            .first()
        )
        if row is None or row.is_expired:
            return Response({"detail": GENERIC_CODE_FAILURE}, status=401)

        _enforce(ratelimit.hit(
            PLATFORM_CODE_RESEND.bucket, row.challenge_hash,
            limit=PLATFORM_CODE_RESEND.limit, window=PLATFORM_CODE_RESEND.window,
        ))
        _enforce(ratelimit.hit(
            PLATFORM_CODE_PER_ACCOUNT.bucket, str(row.user_id),
            limit=PLATFORM_CODE_PER_ACCOUNT.limit,
            window=PLATFORM_CODE_PER_ACCOUNT.window,
        ))

        user = row.user
        if not user.is_active or not user.is_platform_admin:
            return Response({"detail": GENERIC_CODE_FAILURE}, status=401)

        challenge, delivery = issue_and_send(
            user, PlatformCodePurpose.LOGIN, request=request
        )
        if delivery != CodeDelivery.SENT:
            return Response({"detail": _DELIVERY_FAILED}, status=503)

        return Response({
            "requires_code": True,
            "challenge": challenge,
            "expires_in": int(PlatformEmailCode.TTL.total_seconds()),
            "sent_to": _mask(user.email),
        })


class PlatformForgotPasswordView(APIView):
    """
    Stage one of password recovery. Answers identically for every address.

    An address with no platform account still receives a challenge token and a
    200. It is a decoy: no row backs it, so any code entered against it fails
    at `verify_code`'s unknown-challenge branch. The alternative — 404 for
    unknown addresses — publishes the list of platform administrators to anyone
    with a form and a word list.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PlatformForgotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        # Counted for every request, real account or not. A limit that applied
        # only to real accounts would itself answer the question.
        _enforce(ratelimit.hit(
            PLATFORM_RESET_PER_EMAIL.bucket, _email_key(email),
            limit=PLATFORM_RESET_PER_EMAIL.limit,
            window=PLATFORM_RESET_PER_EMAIL.window,
        ))

        user = eligible_platform_admin(email)
        if user is None:
            logger.info("Platform reset requested for a non-platform address.")
            return Response(_decoy_response())

        challenge, delivery = issue_and_send(
            user, PlatformCodePurpose.PASSWORD_RESET, request=request
        )
        if delivery != CodeDelivery.SENT:
            # Still a uniform body. Reporting a delivery failure only for real
            # accounts would be an oracle.
            logger.error("Platform reset code for %s could not be delivered.", user.pk)
        return Response({
            "challenge": challenge,
            "expires_in": int(PlatformEmailCode.TTL.total_seconds()),
        })


def _decoy_response() -> dict:
    """A challenge-shaped answer that no row backs."""
    import secrets

    return {
        "challenge": secrets.token_urlsafe(32),
        "expires_in": int(PlatformEmailCode.TTL.total_seconds()),
    }


class PlatformResetPasswordView(APIView):
    """
    Stage two of password recovery.

    Deliberately does NOT sign the operator in. A reset proves control of the
    mailbox, not possession of the new password, and the console is reached
    through the normal two-stage login afterwards.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PlatformResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user, reason = verify_code(
            data["challenge"], data["code"], PlatformCodePurpose.PASSWORD_RESET
        )
        if user is None:
            logger.warning("Platform reset verification failed (%s).", reason)
            return Response({"detail": GENERIC_CODE_FAILURE}, status=401)

        try:
            validate_password(data["new_password"], user=user)
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=400)

        with transaction.atomic():
            user.set_password(data["new_password"])
            user.save(update_fields=["password"])
            # Order matters: the password is already changed, so any challenge
            # still in flight now fails its fingerprint check anyway. Closing
            # them explicitly leaves an audit trail that says "closed by reset"
            # rather than a row that merely stops working.
            invalidate_outstanding(user)
            revoked = revoke_all_refresh_tokens(user)

        logger.info(
            "Platform password reset completed for %s; %d refresh tokens revoked.",
            user.pk, revoked,
        )
        _audit_platform_security(request, user, "platform.password_reset", actor=user)

        # Any refresh cookie this browser still holds belongs to a session that
        # was just revoked. Clearing it avoids a confusing 401 loop on the next
        # page load.
        return clear_refresh_cookie(Response({
            "detail": "Password changed. Sign in with your new password.",
            "sessions_revoked": revoked,
        }))


def _audit_platform_security(request, subject_user, action: str, *, actor) -> None:
    """
    Record a platform security event.

    Platform actions are not scoped to an organization, and `MailLog` requires
    a tenant, so these go to the platform audit log instead. Failures here are
    not swallowed: an audit record that silently does not exist is worse than
    an error, because the absence reads as "nothing happened".
    """
    from .audit import record_platform_action

    record_platform_action(
        actor=actor,
        action=action,
        request=request,
        target_user=subject_user,
    )
