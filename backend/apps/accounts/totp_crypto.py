"""Independent, encrypted-at-rest Hub TOTP secrets.

The operational Fernet key is required for every enabled Hub TOTP account.
Plaintext legacy values are never accepted at runtime after the migration.
The data migration encrypts existing secrets transactionally, preserving 2FA.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
import pyotp

PREFIX = "enc:v1:"


def _cipher():
    key = getattr(settings, "HUB_2FA_FERNET_KEY", "")
    if not key:
        raise ImproperlyConfigured("HUB_2FA_FERNET_KEY is required for Hub TOTP.")
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured("HUB_2FA_FERNET_KEY is malformed.") from exc


def encrypt_secret(raw):
    if not isinstance(raw, str) or not (len(raw) == 32 and
                                       set(raw).issubset(set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"))):
        raise ValueError("Invalid TOTP secret")
    return PREFIX + _cipher().encrypt(raw.encode("ascii")).decode("ascii")


def decrypt_secret(value):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        raise ValueError("Unencrypted Hub TOTP secret refused.")
    try:
        raw = _cipher().decrypt(value[len(PREFIX):].encode("ascii")).decode("ascii")
    except (ValueError, InvalidToken, UnicodeError, AttributeError):
        raise ValueError("Unable to decrypt Hub TOTP secret.") from None
    if not (len(raw) == 32 and set(raw).issubset(set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"))):
        raise ValueError("Invalid Hub TOTP secret.")
    return raw


def create_secret():
    raw = pyotp.random_base32()
    return encrypt_secret(raw), raw
