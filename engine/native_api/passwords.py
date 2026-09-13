"""
Mailbox password hashing.

THE ONLY PLACE A PLAINTEXT MAILBOX PASSWORD EXISTS
    It arrives in one request body, becomes a hash here, and the plaintext is
    never written to the database, to Redis, to a log line, or to a response.
    Nothing in this module accepts a password into a formatted string.

SCHEME
    BLF-CRYPT (bcrypt), stored with Dovecot's scheme prefix so the stored value
    is self-describing:

        {BLF-CRYPT}$2b$10$....

    VERIFIED against the pinned image rather than assumed. `doveadm pw -l` on
    dovecot/dovecot:2.4.1 lists BLF-CRYPT, and `doveadm pw -t` accepts a hash
    produced by this module's bcrypt — returning success for the right password
    and failure for a wrong one. That check matters because Dovecot's own
    `doveadm pw -s BLF-CRYPT` emits the `$2y$` variant while Python's bcrypt
    emits `$2b$`; both are accepted, but "both are accepted" is a measurement,
    not an assumption.

COST
    Dovecot's own default is cost 5, which is low for a credential that guards a
    mailbox. 10 is the default here — roughly 100 ms, strong enough to make
    offline cracking expensive while staying cheap enough for IMAP clients that
    reauthenticate constantly. Tunable so an operator can raise it without a
    code change; existing hashes keep their own cost and still verify, because
    the cost travels inside the hash.
"""
from __future__ import annotations

import os

import bcrypt

#: Dovecot scheme prefix. The stored value carries it so a reader never has to
#: infer which algorithm produced the hash.
SCHEME = "{BLF-CRYPT}"

_DEFAULT_ROUNDS = 10


def _rounds() -> int:
    raw = os.environ.get("NATIVE_BCRYPT_ROUNDS", "")
    if not raw:
        return _DEFAULT_ROUNDS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_ROUNDS
    # bcrypt itself refuses outside 4..31; clamping to a sane floor stops a
    # typo'd "1" from producing a trivially crackable hash.
    return min(31, max(8, value))


def hash_password(plaintext: str) -> str:
    """
    Hash a mailbox password for storage.

    Takes the plaintext, returns the scheme-prefixed hash, and keeps no
    reference to either beyond the call. Raises on an empty password rather than
    producing a hash for one — the adapter contract rejects empty passwords, and
    a hash of "" is a working credential.
    """
    if not plaintext:
        raise ValueError("refusing to hash an empty password")
    digest = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(rounds=_rounds()))
    return SCHEME + digest.decode("ascii")


def verify_password(plaintext: str, stored: str) -> bool:
    """
    Check a plaintext against a stored hash.

    Present for tests and for NE3's future use; Dovecot does its own verification
    against the same stored value. Returns False for anything malformed rather
    than raising, so a corrupt row denies access instead of crashing the caller.
    """
    if not plaintext or not stored:
        return False
    if not stored.startswith(SCHEME):
        return False
    digest = stored[len(SCHEME):].encode("ascii", errors="ignore")
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), digest)
    except (ValueError, TypeError):
        return False


def is_hashed(value: str) -> bool:
    """
    Does this look like something we hashed, rather than a raw password?

    Used as a last assertion before a write. The database has the same check as
    a constraint; this one exists so the error names the mailbox instead of the
    column.
    """
    return isinstance(value, str) and value.startswith(SCHEME) and len(value) > len(SCHEME) + 20
