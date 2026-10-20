"""Enrollment-only, password-verified Hub 2FA bootstrap.

A tenant's Require policy must not issue a full JWT to an unenrolled account.
The partial login challenge is an opaque cache-bound capability which may ONLY
set up and verify TOTP; it is never accepted as bearer authentication.
"""
import pyotp

from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.security import ratelimit
from apps.security.limits import TWO_FACTOR_PER_USER, TOTP_REPLAY_TTL
from apps.security.throttling import AuthEndpointThrottle
from apps.tenants.host_binding import scope_memberships
from apps.tenants.models import TenantMembership, MemberStatus, OrganizationTwoFactorMode
from apps.tenants.security_policy import effective_two_factor_policy
from apps.tenants.admin_hub_mfa_policy import effective_admin_hub_mfa_policy
from apps.tenants.models import AdminHubMfaMode

from .totp_crypto import encrypt_secret, decrypt_secret
from .challenge import (
    peek_challenge, challenge_matches_password, consume_challenge,
    register_failed_attempt, discard_challenge,
)
from .models import User, TwoFactorSetup, TwoFactorBackupCode
from .tokens import generate_backup_codes, hash_token, make_tokens
from .views import authenticated_response, _tenant_brief
from .serializers import UserProfileSerializer


class EnrollmentInput(serializers.Serializer):
    partial_token = serializers.CharField(min_length=32, max_length=256)
    action = serializers.ChoiceField(choices=("begin", "confirm"))
    code = serializers.CharField(required=False, min_length=6, max_length=6)


class RequiredTwoFactorEnrollmentView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthEndpointThrottle]
    authentication_classes = []

    def post(self, request):
        data = EnrollmentInput(data=request.data)
        data.is_valid(raise_exception=True)
        value = data.validated_data
        raw_token = value["partial_token"]
        challenge = peek_challenge(raw_token)
        if challenge is None:
            return Response({"detail": "Invalid or expired challenge."}, status=401)

        user = User.objects.filter(id=challenge.get("user_id"), is_active=True).first()
        if user is None or not challenge_matches_password(challenge, user):
            discard_challenge(raw_token)
            return Response({"detail": "Invalid or expired challenge."}, status=401)
        membership = scope_memberships(
            request,
            TenantMembership.objects.select_related("tenant").filter(
                user=user, tenant_id=challenge.get("tenant_id"),
                status=MemberStatus.ACTIVE,
            ),
        ).first()
        if membership is None or not (
            user.hub_mfa_reenrollment_required or
            effective_two_factor_policy(membership.tenant) == OrganizationTwoFactorMode.REQUIRE or
            effective_admin_hub_mfa_policy(membership) == AdminHubMfaMode.REQUIRED
        ):
            return Response({"detail": "Not available."}, status=403)
        if user.two_factor_enabled:
            return Response({"detail": "Two-factor enrollment already completed."}, status=409)

        verdict = ratelimit.check(
            TWO_FACTOR_PER_USER.bucket, user.pk,
            limit=TWO_FACTOR_PER_USER.limit, window=TWO_FACTOR_PER_USER.window,
        )
        if not verdict.allowed:
            return Response({"detail": "Too many verification attempts."}, status=429)

        if value["action"] == "begin":
            setup, _ = TwoFactorSetup.objects.get_or_create(
                user=user, defaults={"totp_secret": encrypt_secret(pyotp.random_base32())},
            )
            return Response({
                "secret": decrypt_secret(setup.totp_secret),
                "uri": pyotp.TOTP(decrypt_secret(setup.totp_secret)).provisioning_uri(
                    name=user.email, issuer_name="MateMail",
                ),
            })

        code = value.get("code", "")
        try:
            setup = user.two_factor_setup
        except TwoFactorSetup.DoesNotExist:
            return Response({"detail": "Start enrollment first."}, status=400)
        if not pyotp.TOTP(decrypt_secret(setup.totp_secret)).verify(code, valid_window=1):
            register_failed_attempt(raw_token)
            ratelimit.hit(
                TWO_FACTOR_PER_USER.bucket, user.pk,
                limit=TWO_FACTOR_PER_USER.limit, window=TWO_FACTOR_PER_USER.window,
            )
            return Response({"detail": "Invalid verification code."}, status=401)
        if not ratelimit.claim_once(
            "2fa:totp", f"{user.pk}:{code}", ttl=TOTP_REPLAY_TTL
        ):
            return Response({"detail": "Verification code already used."}, status=401)
        if not consume_challenge(raw_token):
            return Response({"detail": "Invalid or expired challenge."}, status=401)

        codes = generate_backup_codes(10)
        with transaction.atomic():
            user.two_factor_enabled = True
            user.hub_mfa_reenrollment_required = False
            user.save(update_fields=["two_factor_enabled", "hub_mfa_reenrollment_required"])
            TwoFactorBackupCode.objects.filter(user=user).delete()
            TwoFactorBackupCode.objects.bulk_create([
                TwoFactorBackupCode(user=user, code_hash=hash_token(c))
                for c in codes
            ])
        tokens = make_tokens(user, tenant_id=membership.tenant_id, mfa_verified=True)
        return authenticated_response(
            tokens,
            {
                "user": UserProfileSerializer(user).data,
                "tenant": _tenant_brief(membership.tenant),
                "backup_codes": codes,
                "requires_2fa": False,
            },
        )
