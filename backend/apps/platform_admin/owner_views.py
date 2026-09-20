"""
Primary Owner recovery.

    GET  /api/platform/tenants/<id>/owner/                   account metadata
    POST /api/platform/tenants/<id>/owner/password-recovery/ email a reset link
    POST /api/platform/tenants/<id>/owner/revoke-sessions/   end their sessions

WHY THE TARGET IS NEVER A PARAMETER
    Every endpoint here derives its subject from `tenant.owner`. There is no
    request field naming a user, so there is no request that can be made to
    point at somebody else: not another organization's owner, not an ordinary
    member, not another platform administrator. A whole class of mistake — the
    operator with two tabs open who recovers the wrong account — is removed by
    construction rather than by validation.

    That is also why this is not "list the organization's administrators and
    let the operator pick". An organization can have several members with the
    admin role; only one of them created it, and only that relation is what
    recovery is for.

WHAT THIS DELIBERATELY CANNOT DO
    Nothing here reads mail. Recovering an account and reading its mailbox are
    different boundaries, and a platform administrator crosses neither by
    triggering a reset: the link goes to the owner's address, the token is
    never returned in the API response, and the existing password is never
    disclosed or replaced with one the operator knows.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import Throttled

from apps.accounts.mailer import send_transactional
from apps.accounts.models import PasswordResetToken
from apps.accounts.tokens import revoke_all_refresh_tokens
from apps.security import ratelimit
from apps.security.limits import OWNER_RECOVERY_PER_ADMIN
from apps.tenants.models import Tenant
from apps.tenants.permissions import IsPlatformAdmin

from .audit import record_platform_action

logger = logging.getLogger(__name__)


class ReasonSerializer(serializers.Serializer):
    #: Required. These actions touch a customer's account, and "why" is the
    #: question the audit trail exists to answer six months later.
    reason = serializers.CharField(max_length=500, allow_blank=False, trim_whitespace=True)


def _tenant_or_404(pk):
    return Tenant.objects.select_related("owner").filter(pk=pk).first()


def _owner_payload(owner) -> dict:
    """
    Account metadata a platform operator legitimately needs, and nothing else.

    No password hash, no TOTP secret, no backup codes, no session tokens. The
    2FA field is a boolean about whether a second factor exists, which is an
    operational fact; the secret behind it is not.
    """
    return {
        "id": str(owner.id),
        "full_name": owner.full_name,
        "email": owner.email,
        "is_active": owner.is_active,
        "email_verified": owner.email_verified,
        "two_factor_enabled": owner.two_factor_enabled,
        "last_login": owner.last_login.isoformat() if owner.last_login else None,
        "created_at": owner.created_at.isoformat() if owner.created_at else None,
    }


def _guard_rate(request):
    decision = ratelimit.hit(
        OWNER_RECOVERY_PER_ADMIN.bucket, str(request.user.pk),
        limit=OWNER_RECOVERY_PER_ADMIN.limit,
        window=OWNER_RECOVERY_PER_ADMIN.window,
    )
    if not decision.allowed:
        # These actions send mail to a customer. An operator with a stuck
        # script must not be able to turn the console into a way of flooding
        # an organization owner's inbox.
        raise Throttled(
            wait=decision.retry_after,
            detail="Too many recovery actions. Please wait before trying again.",
        )


class PlatformOwnerView(APIView):
    """The organization's Primary Owner, as the console displays them."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request, pk):
        tenant = _tenant_or_404(pk)
        if tenant is None:
            return Response({"detail": "Not found."}, status=404)
        if tenant.owner is None:
            # Real state, not an error: an organization whose owner account was
            # deleted. Saying so plainly is what lets an operator fix it,
            # rather than showing a blank panel that looks like a bug.
            return Response({
                "owner": None,
                "detail": "This organization has no owner account on record.",
            })
        return Response({"owner": _owner_payload(tenant.owner)})


class PlatformOwnerPasswordRecoveryView(APIView):
    """
    Email the Primary Owner a password-reset link.

    Reuses `PasswordResetToken` — the same one-hour, single-use, hashed token
    the customer's own "forgot password" issues. Recovery initiated by an
    operator and recovery initiated by the owner arrive at the same place, so
    there is no second reset path with its own bugs, and no temporary password
    for anybody to mishandle.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        tenant = _tenant_or_404(pk)
        if tenant is None:
            return Response({"detail": "Not found."}, status=404)
        if tenant.owner is None:
            return Response(
                {"detail": "This organization has no owner account to recover."},
                status=409,
            )

        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]

        _guard_rate(request)

        owner = tenant.owner
        raw, _token = PasswordResetToken.make(owner)
        reset_url = f"{settings.FRONTEND_URL}/reset-password?token={raw}"

        delivered = send_transactional(
            subject="Reset your MateMail password",
            body=(
                f"A MateMail administrator has started account recovery for "
                f"your organization, {tenant.name}.\n\n"
                f"Use this link within the next hour to choose a new "
                f"password:\n\n{reset_url}\n\n"
                f"If you were not expecting this, contact MateMail support "
                f"before using the link. Your password has not changed.\n\n"
                f"— MateMail, by NetaMate Solutions"
            ),
            to=owner.email,
            purpose="owner-recovery",
        )

        # `raw` is not returned, not logged, and not put in the audit metadata.
        # The operator's job is to start recovery; only the owner's mailbox
        # finishes it.
        record_platform_action(
            actor=request.user,
            action="owner.password_recovery",
            request=request,
            tenant=tenant,
            target_user=owner,
            target_label=owner.email,
            result="success" if delivered else "delivery_failed",
            reason=reason,
        )

        if not delivered:
            # Surfaced, not swallowed. An operator told "sent" who then waits
            # for a mail that was never accepted is worse off than one told the
            # truth immediately.
            return Response(
                {
                    "detail": (
                        "The reset link could not be sent. Transactional email "
                        "may be misconfigured; the action has been recorded."
                    ),
                    "delivered": False,
                },
                status=503,
            )

        return Response({
            "detail": f"Password recovery sent to {owner.email}.",
            "delivered": True,
        })


class PlatformOwnerRevokeSessionsView(APIView):
    """
    End every session the Primary Owner could still renew.

    For use after a compromise, and after a recovery. Blacklists the owner's
    refresh tokens only — one account, derived from the tenant, so it cannot
    reach a second person.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        tenant = _tenant_or_404(pk)
        if tenant is None:
            return Response({"detail": "Not found."}, status=404)
        if tenant.owner is None:
            return Response(
                {"detail": "This organization has no owner account."}, status=409
            )

        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]

        _guard_rate(request)

        owner = tenant.owner
        revoked = revoke_all_refresh_tokens(owner)

        record_platform_action(
            actor=request.user,
            action="owner.revoke_sessions",
            request=request,
            tenant=tenant,
            target_user=owner,
            target_label=owner.email,
            reason=reason,
            metadata={"refresh_tokens_revoked": revoked},
        )

        logger.info(
            "Platform admin %s revoked %d refresh tokens for owner %s of tenant %s.",
            request.user.pk, revoked, owner.pk, tenant.pk,
        )

        return Response({
            "detail": f"{revoked} session(s) revoked for {owner.email}.",
            "sessions_revoked": revoked,
            # Already-issued access tokens stay valid until they expire; saying
            # so stops an operator believing the account was cut off instantly.
            "access_token_lifetime_note": (
                "Existing access tokens remain valid until they expire "
                f"({int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds() // 60)} minutes)."
            ),
            "revoked_at": timezone.now().isoformat(),
        })
