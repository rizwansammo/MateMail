import pyotp
from django.core.exceptions import ValidationError

from apps.accounts.totp_crypto import decrypt_secret
from apps.accounts.models import TwoFactorBackupCode, TwoFactorSetup
from apps.accounts.tokens import hash_token
from apps.security import ratelimit
from apps.security.limits import TOTP_REPLAY_TTL


def mailbox_verification_requirement(mailbox):
    """Only new connections need a fresh mailbox factor; existing mmc_ grants stay valid.

    Use the same live policy boundary as PostBox login. A workspace Require
    policy cannot be bypassed by approving an app for an unenrolled mailbox.
    """
    from apps.postbox.auth import mailbox_requires_two_factor

    return {
        "required": bool(mailbox_requires_two_factor(mailbox)),
        "label": "Mailbox authenticator or backup code",
    }


def verify_mailbox_factor(mailbox, code=""):
    if not mailbox_verification_requirement(mailbox)["required"]:
        return

    from apps.postbox.models import MailboxTwoFactorSetup
    from apps.postbox.two_factor_core import redeem_backup_code, verify_totp
    from apps.security.limits import POSTBOX_PASSWORD_CHANGE

    # Failure must not silently authorize a new connected app when an enrolled
    # mailbox's 2FA setup is inaccessible or the organization requires MFA but
    # the mailbox has not enrolled yet.
    setup = MailboxTwoFactorSetup.objects.filter(
        mailbox=mailbox, enabled_at__isnull=False,
    ).first()
    if setup is None:
        raise ValidationError({
            "mailbox_factor_code": "Set up mailbox two-factor authentication before approving this connection."
        })

    # Unlike an existing operational mmc_ token, approval is interactive and
    # subject to the mailbox's own per-account attempt budget.
    limit = ratelimit.hit(
        POSTBOX_PASSWORD_CHANGE.bucket, str(mailbox.pk),
        limit=POSTBOX_PASSWORD_CHANGE.limit,
        window=POSTBOX_PASSWORD_CHANGE.window,
    )
    if not limit.allowed:
        raise ValidationError({
            "mailbox_factor_code": "Too many mailbox security attempts. Try again later."
        })

    raw_code = (code or "").strip()
    if not (verify_totp(setup, raw_code) or redeem_backup_code(mailbox, raw_code)):
        raise ValidationError({"mailbox_factor_code": "Invalid mailbox verification code."})


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

        totp = pyotp.TOTP(decrypt_secret(setup.totp_secret))
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
