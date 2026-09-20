import pyotp
from django.core.exceptions import ValidationError

from apps.accounts.models import TwoFactorBackupCode, TwoFactorSetup
from apps.accounts.tokens import hash_token
from apps.security import ratelimit
from apps.security.limits import TOTP_REPLAY_TTL


def mailbox_verification_requirement(mailbox):
    """
    Future-proof boundary for mailbox-level 2FA.

    Mailboxes do not currently carry a second factor. When that feature lands,
    this function and verify_mailbox_factor are the only integration-facing
    pieces that need to change; SalesHub already understands this generic step.
    """
    return {"required": False, "label": "Mailbox verification code"}


def verify_mailbox_factor(mailbox, code=""):
    requirement = mailbox_verification_requirement(mailbox)
    if not requirement["required"]:
        return
    raise ValidationError(
        {"mailbox_factor_code": "Mailbox verification is not configured."}
    )


def verify_fresh_authorization(
    *,
    user,
    integration,
    password,
    two_factor_code="",
    mailbox_factor_code="",
):
    if not user.check_password(password or ""):
        raise ValidationError({"password": "Incorrect password."})

    if user.two_factor_enabled:
        code = (two_factor_code or "").strip()
        setup = TwoFactorSetup.objects.filter(user=user).first()
        if not setup or not code:
            raise ValidationError(
                {"two_factor_code": "Enter your authentication code."}
            )

        totp = pyotp.TOTP(setup.totp_secret)
        backup = None
        if totp.verify(code, valid_window=1):
            if not ratelimit.claim_once(
                "2fa:totp", f"{user.pk}:{code}", ttl=TOTP_REPLAY_TTL
            ):
                raise ValidationError(
                    {"two_factor_code": "Invalid authentication code."}
                )
        else:
            backup = TwoFactorBackupCode.objects.filter(
                user=user,
                code_hash=hash_token(code),
                is_used=False,
            ).first()
            if not backup:
                raise ValidationError(
                    {"two_factor_code": "Invalid authentication code."}
                )

        if backup is not None:
            backup.is_used = True
            backup.save(update_fields=["is_used"])

    verify_mailbox_factor(integration.mailbox, mailbox_factor_code)
