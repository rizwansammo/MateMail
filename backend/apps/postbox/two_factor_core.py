"""Cryptographic building blocks for PostBox mailbox MFA (Stage 7A).

Mailbox MFA is NOT Hub user MFA. These helpers are deliberately not exposed
by HTTP until trusted web-password verification and Native SMTP/IMAP enforcement
are implemented; enabling MFA earlier would leave a public IMAP bypass.
"""
from __future__ import annotations

import hashlib
import re
import secrets

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from apps.security import ratelimit

from .models import MailboxTwoFactorBackupCode


def enrollment_ready() -> bool:
    # Release barrier: no config-only flag can activate incomplete protection.
    return False


def _cipher() -> Fernet:
    key = getattr(settings, "POSTBOX_2FA_FERNET_KEY", "")
    if not key:
        raise ImproperlyConfigured("PostBox 2FA encryption key is not configured.")
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("PostBox 2FA encryption key is invalid.") from exc


def encrypt_totp_secret(secret: str) -> str:
    if not isinstance(secret, str) or not re.fullmatch(r"[A-Z2-7]{32}", secret):
        raise ValueError("Invalid authenticator secret.")
    return _cipher().encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_totp_secret(encrypted: str) -> str:
    # A wrong/rotated key must fail, never reset MFA to an unprotected state.
    try:
        secret = _cipher().decrypt(encrypted.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError, AttributeError, ValueError):
        raise ValueError("Authenticator secret cannot be decrypted.") from None
    if not re.fullmatch(r"[A-Z2-7]{32}", secret):
        raise ValueError("Invalid authenticator secret.")
    return secret


def verify_totp(setup, code: str) -> bool:
    if setup.enabled_at is None or not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        return False
    secret = decrypt_totp_secret(setup.totp_secret_encrypted)
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return False
    # A 6-digit OTP is reusable inside TOTP's time window unless claimed.
    return ratelimit.claim_once(
        "postbox:2fa:totp", f"{setup.mailbox_id}:{code}", ttl=120
    )


def generate_backup_codes(count: int = 10) -> list[str]:
    if not isinstance(count, int) or not 1 <= count <= 20:
        raise ValueError("Recovery code count out of range.")
    # 24 random bytes each. Offline hashes remain safe even if DB leaks.
    return [secrets.token_urlsafe(24) for _ in range(count)]


def hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def redeem_backup_code(mailbox, raw_code: str) -> bool:
    if not isinstance(raw_code, str) or len(raw_code) < 24 or len(raw_code) > 64:
        return False
    with transaction.atomic():
        row = MailboxTwoFactorBackupCode.objects.select_for_update().filter(
            mailbox=mailbox,
            code_hash=hash_backup_code(raw_code),
            used_at__isnull=True,
        ).first()
        if row is None:
            return False
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        return True
