"""
Interim encryption for Django-held DKIM private keys.

**This is temporary.** DEC-007r is final and unchanged: P4 moves DKIM key
generation and storage into the Mail Engine and removes
`Domain.dkim_private_key` from Django completely. Nothing new should be built
on the assumption that Django holds these keys — this module exists only so
that the column which exists *today* is not plaintext in a database backup
until P4 removes it.

A DKIM private key is the authority to sign mail as the customer's domain.
Anyone holding it can send mail that passes DKIM for that domain, which is the
whole basis of the domain's sending reputation. Sitting in a `TextField` it was
readable by anything with database access — a backup, a read replica, a
snapshot, an accidental `dumpdata`.

Design:

- **Fernet (AES-128-CBC + HMAC-SHA256)** from `cryptography`, which is already
  a dependency. Authenticated, so a tampered ciphertext fails loudly rather
  than decrypting to garbage that would silently produce invalid signatures.
- **A dedicated key**, `DKIM_ENCRYPTION_KEY`, deliberately *not* derived from
  `SECRET_KEY`. `SECRET_KEY` signs sessions and JWTs and will eventually be
  rotated for reasons that have nothing to do with DKIM; a rotation that
  silently made every stored DKIM key undecryptable would be discovered by
  customers, as mail failing authentication.
- **Versioned prefix** on the stored value, so rotation is possible and so a
  row can be identified as encrypted or not without guessing.
- **Read-through for legacy plaintext.** Rows written before this change are
  PEM text. They are detected and returned as-is rather than being treated as
  corrupt, and the data migration rewrites them. Nothing is ever discarded.

Rotation: `DKIM_ENCRYPTION_KEYS` accepts a comma-separated list. The first is
used to encrypt; all are tried when decrypting. To rotate, prepend the new key,
re-save the rows, then drop the old one.
"""
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

#: Marks a value written by this module. A stored value without it is either
#: empty or legacy plaintext PEM.
PREFIX = "dkimv1:"

#: How a PEM private key always begins.
_PEM_MARKER = "-----BEGIN"


class DkimKeyUnavailable(RuntimeError):
    """The stored key cannot be read with the configured encryption keys."""


def _configured_keys() -> list[str]:
    keys = [
        k.strip()
        for k in (getattr(settings, "DKIM_ENCRYPTION_KEYS", "") or "").split(",")
        if k.strip()
    ]
    return keys


def encryption_configured() -> bool:
    return bool(_configured_keys())


def _fernets():
    from cryptography.fernet import Fernet

    keys = _configured_keys()
    if not keys:
        raise ImproperlyConfigured(
            "DKIM_ENCRYPTION_KEY is not set. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"\n"
            "and set it in the environment. It must be separate from "
            "DJANGO_SECRET_KEY."
        )
    return [Fernet(k.encode()) for k in keys]


def is_encrypted(stored: str) -> bool:
    return bool(stored) and stored.startswith(PREFIX)


def looks_like_plaintext_key(stored: str) -> bool:
    return bool(stored) and _PEM_MARKER in stored


def encrypt(private_pem: str) -> str:
    """
    Encrypt a PEM private key for storage. Empty input stays empty.

    When no key is configured this returns the plaintext and logs an error
    rather than raising. Raising would mean a deployment without the variable
    could not add a domain at all, and an outage is a worse answer to a
    misconfiguration than a loud log plus a failing system check — see
    apps.domains.checks, which makes this configuration an error under
    DEBUG=False, so it cannot reach production unnoticed.
    """
    if not private_pem:
        return ""
    if is_encrypted(private_pem):
        return private_pem  # already sealed; do not double-wrap

    if not encryption_configured():
        logger.error(
            "DKIM_ENCRYPTION_KEY is not set — storing a DKIM private key in "
            "PLAINTEXT. Set it and run `manage.py migrate domains` to encrypt "
            "existing rows."
        )
        return private_pem

    token = _fernets()[0].encrypt(private_pem.encode())
    return PREFIX + token.decode()


def decrypt(stored: str) -> str:
    """
    Return the PEM private key.

    Legacy plaintext rows are returned unchanged so that a deployment which
    has not yet run the data migration keeps working — the alternative is
    breaking DKIM signing for every existing domain at the moment of upgrade.
    """
    if not stored:
        return ""

    if not is_encrypted(stored):
        if looks_like_plaintext_key(stored):
            logger.warning(
                "A DKIM private key is still stored in plaintext. Run "
                "`manage.py migrate domains` to encrypt existing rows."
            )
            return stored
        raise DkimKeyUnavailable("Stored DKIM key is neither encrypted nor a PEM key.")

    from cryptography.fernet import InvalidToken

    payload = stored[len(PREFIX):].encode()
    for fernet in _fernets():
        try:
            return fernet.decrypt(payload).decode()
        except InvalidToken:
            continue

    # Every configured key was tried and none worked. This is loud on purpose:
    # returning "" would look like "this domain has no DKIM key" and would be
    # silently provisioned as unsigned mail.
    raise DkimKeyUnavailable(
        "Stored DKIM key could not be decrypted with any configured "
        "DKIM_ENCRYPTION_KEY. The key material may have been rotated without "
        "re-encrypting, or the value is corrupt."
    )
