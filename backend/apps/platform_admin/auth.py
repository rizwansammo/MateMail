"""
Platform Console authentication — mandatory emailed second factor.

WHY THIS IS SEPARATE FROM THE TENANT SECOND FACTOR
    A tenant user's second factor is optional and TOTP-based: they enrol if
    they choose to, and `User.two_factor_enabled` records that choice. A
    platform administrator has no such choice. The console behind this login
    can suspend every organization on the platform, so the emailed code is
    required on every login, for every platform account, with no enrolment step
    that could be skipped and no flag that could be turned off.

    That difference is why this is not expressed as "turn two_factor_enabled on
    for admins". A boolean that is checked is a boolean that can be false.
    Here the requirement is structural: this module has no branch that issues
    credentials without a verified code.

WHAT IS AND IS NOT A CREDENTIAL
    The challenge token returned after a correct password is deliberately
    opaque — 32 bytes of URL-safe entropy, carrying no claims. DRF's
    JWTAuthentication cannot parse it, so it cannot be replayed as an access
    token. No access token and no refresh cookie exist anywhere in this flow
    until `verify_code` has succeeded.

SEE ALSO
    apps.accounts.challenge — the same shape for the tenant TOTP step.
    DEC-046 in docs/DECISIONS.md.
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.mailer import send_transactional
from apps.accounts.models import PlatformCodePurpose, PlatformEmailCode

logger = logging.getLogger(__name__)
User = get_user_model()


class CodeDelivery:
    """Why an issued code could not be delivered, for the caller to act on."""

    SENT = "sent"
    NOT_CONFIGURED = "not_configured"
    FAILED = "failed"


#: One message for every failure in this flow. A caller that distinguished
#: "no such account" from "wrong password" from "not a platform admin" would
#: be telling an attacker which of the three to work on, and the set of
#: platform administrators is exactly what must not be discoverable.
GENERIC_FAILURE = "Invalid credentials."

#: Likewise for the verification step.
GENERIC_CODE_FAILURE = "That code is not valid."


def eligible_platform_admin(email: str) -> User | None:
    """
    The account this address belongs to, if it may use the Platform Console.

    Returns None for an address that does not exist, an account that is not a
    platform administrator, and one that has been deactivated. The caller must
    answer identically in all three cases.
    """
    user = User.objects.filter(email__iexact=(email or "").strip()).first()
    if user is None or not user.is_active or not user.is_platform_admin:
        return None
    return user


def issue_and_send(user, purpose: str, *, request=None) -> tuple[str, str]:
    """
    Create a challenge, email its code, and return (raw_challenge, delivery).

    The raw code is passed to the mailer and then dropped. It is never
    returned to the caller, never logged, and never stored — the row holds only
    a digest salted with the challenge token.
    """
    raw_challenge, code, row = PlatformEmailCode.issue(user, purpose)

    subject = (
        "MateMail Platform Console security code"
        if purpose == PlatformCodePurpose.LOGIN
        else "MateMail Platform Console password reset code"
    )

    # Deliberately plain text. A security code that arrives as a marketing
    # layout is a security code that trains people to trust marketing layouts,
    # and heavy HTML is what puts transactional mail in a spam folder.
    body = (
        f"Your MateMail Platform Console security code is:\n\n"
        f"    {code}\n\n"
        f"This code expires in {int(PlatformEmailCode.TTL.total_seconds() // 60)} minutes "
        f"and can be used once.\n\n"
        f"If you did not attempt to sign in, you can ignore this message. "
        f"Someone entering your address alone cannot reach the console.\n\n"
        f"MateMail\n"
        f"NetaMate Solutions\n"
    )

    # `code` is a local that dies with this frame. Nothing below may log it,
    # and `send_transactional` logs subject and recipient only.
    delivered = send_transactional(
        subject=subject,
        body=body,
        to=user.email,
        purpose=f"platform-{purpose}",
    )

    if not delivered:
        # The row stays: an operator investigating "I never got the code"
        # needs to see that one was issued and that delivery, not issuance,
        # is what failed.
        logger.error(
            "Platform %s code for user %s was issued but not delivered.",
            purpose, user.pk,
        )
        return raw_challenge, CodeDelivery.FAILED

    logger.info("Platform %s code issued for user %s (row %s).", purpose, user.pk, row.pk)
    return raw_challenge, CodeDelivery.SENT


def verify_code(raw_challenge: str, code: str, purpose: str) -> tuple[User | None, str]:
    """
    Check a code against its challenge and consume it.

    Returns (user, "") on success and (None, reason) otherwise. Every failure
    path answers with the same message; `reason` is for logging, never for the
    response body.

    Fails closed on every condition: unknown challenge, wrong purpose, expired,
    already consumed, too many attempts, password changed since issue, account
    deactivated or demoted since issue.
    """
    if not raw_challenge or not code:
        return None, "missing"

    row = (
        PlatformEmailCode.objects.select_related("user")
        .filter(
            challenge_hash=PlatformEmailCode.hash_challenge(raw_challenge),
            purpose=purpose,
        )
        .first()
    )
    if row is None:
        # Also the path for a challenge handed out for an address that has no
        # platform account at all — see `issue_decoy_challenge`.
        return None, "unknown-challenge"

    if row.is_consumed:
        return None, "already-consumed"
    if row.is_expired:
        return None, "expired"
    if row.attempts >= PlatformEmailCode.MAX_ATTEMPTS:
        return None, "attempts-exhausted"

    user = row.user

    # Re-checked at verification, not only at issue. An account demoted or
    # disabled while a challenge was outstanding must not be able to complete
    # it, and that window is exactly when it matters.
    if not user.is_active or not user.is_platform_admin:
        row.consumed_at = timezone.now()
        row.save(update_fields=["consumed_at"])
        return None, "not-eligible"

    if row.password_fingerprint != PlatformEmailCode.password_fingerprint_for(user):
        row.consumed_at = timezone.now()
        row.save(update_fields=["consumed_at"])
        return None, "password-changed"

    if not row.matches(raw_challenge, code):
        row.attempts += 1
        fields = ["attempts"]
        if row.attempts >= PlatformEmailCode.MAX_ATTEMPTS:
            # Burn it rather than leaving a challenge that answers
            # "attempts-exhausted" forever.
            row.consumed_at = timezone.now()
            fields.append("consumed_at")
        row.save(update_fields=fields)
        return None, "wrong-code"

    # Single use. The update is conditional on the row still being unconsumed,
    # so two requests racing with the same correct code produce exactly one
    # winner rather than two sessions.
    claimed = PlatformEmailCode.objects.filter(
        pk=row.pk, consumed_at__isnull=True
    ).update(consumed_at=timezone.now())
    if not claimed:
        return None, "raced"

    return user, ""


def invalidate_outstanding(user, purpose: str | None = None) -> int:
    """
    Consume every live challenge for an account.

    Called after a password reset: the reset itself already voids challenges
    through the password fingerprint, but leaving rows that merely *fail*
    rather than rows that are closed makes the audit trail harder to read.
    """
    qs = PlatformEmailCode.objects.filter(user=user, consumed_at__isnull=True)
    if purpose:
        qs = qs.filter(purpose=purpose)
    return qs.update(consumed_at=timezone.now())
