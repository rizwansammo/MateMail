"""Fixed four-system-role activation after a verified operations canary.

Credentials are generated and owned by MateServer, never passed in HTTP or logs.
Only exact dedicated role names are accepted, with an independent rollback.
"""
from __future__ import annotations

import os
import pathlib
import stat

import db
import passwords
import platform_canary
import platform_security
from validation import ValidationError

ROLES = ("noreply", "security", "dmarc", "tlsrpt")
ACTIVATE = {"domain": "system.matemail.pro", "operation": "activate_remaining_system_roles"}
DEACTIVATE = {"domain": "system.matemail.pro", "operation": "deactivate_remaining_system_roles"}


def _enabled(name: str) -> bool:
    return (platform_security.enabled()
            and platform_security.credentials_distinct()
            and os.environ.get(name, "False").lower() == "true")


def _read_role_secret(role: str) -> str:
    if role not in ROLES:
        raise PermissionError("unrecognised system role")
    path = pathlib.Path("/run/secrets") / f"matemail-system-{role}"
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid not in (0, os.geteuid())
                or info.st_mode & 0o077
                or info.st_size < 48 or info.st_size > 256):
            raise PermissionError("invalid system credential metadata")
        with os.fdopen(fd, "r", encoding="ascii") as content:
            fd = -1
            secret = content.read(257).rstrip("\n")
    finally:
        if fd >= 0:
            os.close(fd)
    if not 48 <= len(secret) <= 256 or not secret.isascii():
        raise PermissionError("invalid system credential")
    return secret


def activate_remaining(body: dict) -> dict:
    if not _enabled("NATIVE_SYSTEM_REMAINING_ACTIVATION_ENABLED"):
        raise PermissionError("remaining system roles activation disabled")
    if not isinstance(body, dict) or body != ACTIVATE:
        raise ValidationError("only four reserved system roles may be activated")
    platform_canary._check_signer()
    hashes = []
    for role in ROLES:
        secret = _read_role_secret(role)
        hashes.append(passwords.hash_password(secret))
        del secret
    with db.connect_platform() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.platform_activate_remaining_system_roles(%s,%s,%s,%s)",
                tuple(hashes),
            )
            result = cur.fetchone()[0]
        conn.commit()
    return result


def deactivate_remaining(body: dict) -> dict:
    if not _enabled("NATIVE_SYSTEM_REMAINING_ROLLBACK_ENABLED"):
        raise PermissionError("remaining system roles rollback disabled")
    if not isinstance(body, dict) or body != DEACTIVATE:
        raise ValidationError("only four reserved system roles may be deactivated")
    with db.connect_platform() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT public.platform_deactivate_remaining_system_roles()")
            result = cur.fetchone()[0]
        conn.commit()
    return result
