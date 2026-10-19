"""PostBox MFA and dedicated SMTP/IMAP password backend (Phase 7A).

All routes are feature-gated OFF by default. Login challenges are opaque and
never create sessions before MFA. Native Engine independently enforces client
credentials; no SMTP/IMAP password is accepted as a web login password.
"""
from __future__ import annotations

import pyotp
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.mail_engine.factory import get_adapter
from apps.mail_engine.errors import EngineSessionDisconnectUnverified
from apps.security import ratelimit
from apps.security.client_ip import get_client_ip
from apps.security.limits import POSTBOX_PASSWORD_CHANGE
from apps.tenants.host_binding import tenant_matches_request
from apps.tenants.models import OrganizationTwoFactorMode
from apps.tenants.security_policy import effective_two_factor_policy
from . import auth as postbox_auth
from . import two_factor_challenge as challenges
from .models import (
    MailboxTwoFactorSetup, MailboxTwoFactorBackupCode,
    MailClientAccessPolicy, PostBoxSession,
)
from .two_factor_core import (
    decrypt_totp_secret, encrypt_totp_secret, generate_backup_codes,
    hash_backup_code, redeem_backup_code, verify_totp,
)
from .views_auth import complete_postbox_login


def _ready():
    return (
        getattr(settings, "POSTBOX_2FA_ENABLED", False)
        and getattr(settings, "SMTP_IMAP_SECURITY_ENABLED", False)
        and getattr(settings, "MAIL_ENGINE_ADAPTER", "") == "native"
    )


def _gate():
    if not _ready():
        return Response({"detail": "Mailbox security is not yet available."}, status=503)
    return None


def _challenge_mailbox(request, raw):
    payload = challenges.peek_challenge(raw)
    if payload is None:
        return None
    from apps.mailboxes.models import Mailbox
    mailbox = Mailbox.objects.select_related("tenant", "domain").filter(
        pk=payload.get("mailbox_id"),
    ).first()
    if mailbox is None or not tenant_matches_request(request, mailbox.tenant):
        return None
    try:
        postbox_auth.assert_mailbox_may_sign_in(mailbox)
    except postbox_auth.MailboxUnavailable:
        return None
    return mailbox, payload


def _rate_limit(mailbox):
    outcome = ratelimit.hit(
        POSTBOX_PASSWORD_CHANGE.bucket, str(mailbox.pk),
        limit=POSTBOX_PASSWORD_CHANGE.limit,
        window=POSTBOX_PASSWORD_CHANGE.window,
    )
    return outcome.allowed


def _require_recent_primary_and_factor(request, password, code):
    mailbox = request.postbox_session.mailbox
    if not _rate_limit(mailbox):
        return False
    if not postbox_auth.trusted_primary_authenticate(mailbox.email, password):
        return False
    setup = MailboxTwoFactorSetup.objects.filter(mailbox=mailbox).first()
    if setup is not None and setup.enabled_at is not None:
        return verify_totp(setup, code)
    # If a Require policy is effective, a password alone can never manage access.
    return effective_two_factor_policy(mailbox.tenant) != OrganizationTwoFactorMode.REQUIRE


class ChallengeInput(serializers.Serializer):
    partial_token = serializers.CharField(min_length=32, max_length=128)
    code = serializers.CharField(required=False, min_length=6, max_length=64)


class PostBoxTwoFactorVerifyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        refused = _gate()
        if refused:
            return refused
        serializer = ChallengeInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data["partial_token"]
        identity = _challenge_mailbox(request, raw)
        if identity is None:
            return Response({"detail": "Invalid or expired challenge."}, status=401)
        mailbox, payload = identity
        setup = MailboxTwoFactorSetup.objects.filter(
            mailbox=mailbox, enabled_at__isnull=False,
        ).first()
        if setup is None:
            return Response({"detail": "Authenticator enrollment required."}, status=409)
        if not _rate_limit(mailbox):
            challenges.reject_attempt(raw)
            return Response({"detail": "Too many attempts."}, status=429)

        code = serializer.validated_data.get("code", "")
        valid = verify_totp(setup, code)
        if not valid:
            valid = redeem_backup_code(mailbox, code)
        if not valid:
            challenges.reject_attempt(raw)
            return Response({"detail": "Invalid verification code."}, status=401)
        if not challenges.consume_challenge(raw):
            return Response({"detail": "Invalid or expired challenge."}, status=401)

        session_token, session = PostBoxSession.issue(
            mailbox, remembered=payload["remember"], mfa_verified=True,
            ip_address=get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        return complete_postbox_login(request, session_token, session)


class EnrollInput(ChallengeInput):
    action = serializers.ChoiceField(choices=("begin", "confirm"))


class PostBoxTwoFactorEnrollView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        refused = _gate()
        if refused:
            return refused
        serializer = EnrollInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data
        raw = fields["partial_token"]
        identity = _challenge_mailbox(request, raw)
        if identity is None:
            return Response({"detail": "Invalid or expired challenge."}, status=401)
        mailbox, payload = identity
        current_setup = MailboxTwoFactorSetup.objects.filter(mailbox=mailbox).first()
        if current_setup and current_setup.enabled_at:
            return Response({"detail": "2FA is already enabled."}, status=409)
        if not _rate_limit(mailbox):
            challenges.reject_attempt(raw)
            return Response({"detail": "Too many attempts."}, status=429)

        if fields["action"] == "begin":
            if current_setup is None:
                secret = pyotp.random_base32()
                current_setup, _ = MailboxTwoFactorSetup.objects.get_or_create(
                    mailbox=mailbox, defaults={
                        "totp_secret_encrypted": encrypt_totp_secret(secret),
                    },
                )
            secret = decrypt_totp_secret(current_setup.totp_secret_encrypted)
            return Response({
                "secret": secret,
                "uri": pyotp.TOTP(secret).provisioning_uri(
                    name=mailbox.email, issuer_name="MateMail PostBox",
                ),
            })

        if current_setup is None:
            return Response({"detail": "Begin enrollment first."}, status=400)
        code = fields.get("code", "")
        secret = decrypt_totp_secret(current_setup.totp_secret_encrypted)
        if not (len(code) == 6 and code.isascii() and code.isdecimal()
                and pyotp.TOTP(secret).verify(code, valid_window=1)):
            challenges.reject_attempt(raw)
            return Response({"detail": "Invalid verification code."}, status=401)
        if not ratelimit.claim_once(
            "postbox:2fa:totp", f"{mailbox.pk}:{code}", ttl=120
        ):
            return Response({"detail": "Verification code already used."}, status=401)
        if not challenges.consume_challenge(raw):
            return Response({"detail": "Invalid or expired challenge."}, status=401)

        # The Native password requirement is committed FIRST. Any failure leaves
        # PostBox MFA pending (never an enrolled account with public password).
        # Initial dedicated credential is generated only AFTER TOTP proof.
        try:
            adapter = get_adapter()
            policy = adapter.list_client_credentials(mailbox.email)
            enabled = policy["enabled"]
            created = (
                adapter.issue_client_credential(mailbox.email, "Initial client")
                if enabled else None
            )
            adapter.set_client_policy(
                mailbox.email, enabled=enabled, dedicated_required=True,
            )
        except Exception:
            return Response({"detail": "Could not secure external mail clients."}, status=503)

        codes = generate_backup_codes()
        with transaction.atomic():
            MailboxTwoFactorSetup.objects.filter(
                mailbox=mailbox, enabled_at__isnull=True,
            ).update(enabled_at=timezone.now())
            MailboxTwoFactorBackupCode.objects.filter(mailbox=mailbox).delete()
            MailboxTwoFactorBackupCode.objects.bulk_create([
                MailboxTwoFactorBackupCode(
                    mailbox=mailbox, code_hash=hash_backup_code(value),
                )
                for value in codes
            ])
            PostBoxSession.objects.filter(mailbox=mailbox, revoked_at__isnull=True).update(
                revoked_at=timezone.now(),
            )
        token, session = PostBoxSession.issue(
            mailbox, remembered=payload["remember"], mfa_verified=True,
            ip_address=get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        response = complete_postbox_login(request, token, session)
        response.data["backup_codes"] = codes
        if created is not None:
            response.data["smtp_imap_password"] = created["one_time_credential"]
            response.data["smtp_imap_password_id"] = created["id"]
        return response


class PostBoxTwoFactorStartView(APIView):
    """Voluntary enrollment requires a *fresh* primary password."""
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refused = _gate()
        if refused:
            return refused
        password = request.data.get("current_password", "")
        mailbox = request.postbox_session.mailbox
        if not _rate_limit(mailbox) or not postbox_auth.trusted_primary_authenticate(
            mailbox.email, password
        ):
            return Response({"detail": "Reauthentication failed."}, status=403)
        if MailboxTwoFactorSetup.objects.filter(
            mailbox=mailbox, enabled_at__isnull=False
        ).exists():
            return Response({"detail": "2FA already enabled."}, status=409)
        return Response({
            "requires_2fa": True,
            "requires_enrollment": True,
            "partial_token": challenges.create_challenge(mailbox),
        })


class ClientCredentialInput(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    code = serializers.CharField(required=False, default="")
    label = serializers.CharField(required=False, max_length=80)
    credential_id = serializers.UUIDField(required=False)
    enabled = serializers.BooleanField(required=False)


class PostBoxClientCredentialView(APIView):
    """Device passwords and protocol toggle, mailbox-scoped and freshly verified."""
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        refused = _gate()
        if refused:
            return refused
        mailbox = request.postbox_session.mailbox
        try:
            payload = get_adapter().list_client_credentials(mailbox.email)
        except Exception:
            return Response({"detail": "Client credentials unavailable."}, status=503)
        enrolled = MailboxTwoFactorSetup.objects.filter(
            mailbox=mailbox, enabled_at__isnull=False
        ).exists()
        # A dedicated-password requirement in the Native Engine is not
        # proof of an enrolled web authenticator (e.g. during recovery).
        return Response({
            **payload,
            "mailbox_address": mailbox.email,
            "two_factor_enabled": enrolled,
            "organization_policy": effective_two_factor_policy(mailbox.tenant),
        })

    def post(self, request):
        refused = _gate()
        if refused:
            return refused
        data = ClientCredentialInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        mailbox = request.postbox_session.mailbox
        if not _require_recent_primary_and_factor(
            request, values["current_password"], values.get("code", "")
        ):
            return Response({"detail": "Security verification failed."}, status=403)
        action = request.data.get("action")
        try:
            adapter = get_adapter()
            if action == "create" and values.get("label"):
                return Response(adapter.issue_client_credential(
                    mailbox.email, values["label"],
                ), status=201)
            if action == "revoke" and values.get("credential_id"):
                return Response(adapter.revoke_client_credential(
                    mailbox.email, str(values["credential_id"]),
                ))
            if action == "access" and "enabled" in values:
                current = adapter.list_client_credentials(mailbox.email)
                updated = adapter.set_client_policy(
                    mailbox.email,
                    enabled=values["enabled"],
                    dedicated_required=current["dedicated_required"],
                )
                MailClientAccessPolicy.objects.update_or_create(
                    mailbox=mailbox,
                    defaults={"smtp_imap_enabled": values["enabled"]},
                )
                return Response(updated)
        except EngineSessionDisconnectUnverified as exc:
            # Native credential state already committed; sessions may persist.
            return Response({
                "detail": exc.customer_message,
                "code": "mail_client_disconnect_unverified",
            }, status=503)
        except Exception:
            return Response({"detail": "Client security operation unavailable."}, status=503)
        return Response({"detail": "Invalid client security operation."}, status=400)


class PostBoxTwoFactorDisableView(APIView):
    """Disable voluntary mailbox MFA only after fresh password plus TOTP proof."""
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refused = _gate()
        if refused:
            return refused
        mailbox = request.postbox_session.mailbox
        if effective_two_factor_policy(mailbox.tenant) == OrganizationTwoFactorMode.REQUIRE:
            return Response({"detail": "This organization requires 2FA."}, status=409)
        setup = MailboxTwoFactorSetup.objects.filter(
            mailbox=mailbox, enabled_at__isnull=False,
        ).first()
        if setup is None:
            return Response({"detail": "2FA is not enabled."}, status=409)
        password = request.data.get("current_password", "")
        code = request.data.get("code", "")
        if not _require_recent_primary_and_factor(request, password, code):
            return Response({"detail": "Security verification failed."}, status=403)
        # There are two separate databases. NEVER make the Native password
        # policy more permissive while Django still considers MFA active:
        # a failed Django transaction would otherwise leave a real bypass.
        try:
            adapter = get_adapter()
            current = adapter.list_client_credentials(mailbox.email)
            for item in current["credentials"]:
                if item["revoked_at"] is None:
                    adapter.revoke_client_credential(mailbox.email, item["id"])
        except Exception:
            return Response({"detail": "Could not revoke external clients."}, status=503)
        with transaction.atomic():
            MailboxTwoFactorBackupCode.objects.filter(mailbox=mailbox).delete()
            MailboxTwoFactorSetup.objects.filter(mailbox=mailbox).delete()
            PostBoxSession.objects.filter(mailbox=mailbox, revoked_at__isnull=True).update(
                revoked_at=timezone.now(),
            )
        try:
            adapter.set_client_policy(
                mailbox.email,
                enabled=current["enabled"],
                dedicated_required=False,
            )
        except Exception:
            # Safe failure: native still requires dedicated credentials, so
            # the regular mailbox password cannot become a protocol bypass.
            return Response({
                "detail": "2FA disabled for PostBox. External mail-client access remains restricted; contact an administrator."
            }, status=503)
        return Response({"detail": "2FA disabled. Sign in again."})
