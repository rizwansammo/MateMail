"""One canary identity only; no generic tenant or platform mutation permissions.

A file-mounted one-time secret never travels in HTTP JSON, URL, headers or logs.
Only a fixed system role becomes active and can be reverted explicitly.
"""
from __future__ import annotations

import os
import pathlib
import stat

import db
import dkim
import passwords
import platform_security
from validation import ValidationError

ACTIVATE_BODY = {
    "domain": "system.matemail.pro",
    "operation": "activate_operations_canary",
}
ROLLBACK_BODY = {
    "domain": "system.matemail.pro",
    "operation": "deactivate_operations_canary",
}
SECRET_PATH = pathlib.Path("/run/secrets/matemail-system-canary-operations")


def _allowed(flag: str) -> bool:
    return (platform_security.enabled()
            and platform_security.credentials_distinct()
            and os.environ.get(flag, "False").lower() == "true")


def _read_canary_secret() -> str:
    # No caller-selected paths, symlinks or readable-by-others credentials.
    fd = os.open(SECRET_PATH, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        mode = os.fstat(fd)
        if (not stat.S_ISREG(mode.st_mode)
                or (mode.st_mode & 0o077) != 0
                or mode.st_uid not in (0, os.geteuid())
                or mode.st_size < 48 or mode.st_size > 256):
            raise PermissionError("invalid canary credential file metadata")
        with os.fdopen(fd, "r", encoding="ascii") as file:
            fd = -1
            content = file.read(257).rstrip("\n")
    finally:
        if fd >= 0:
            os.close(fd)
    if len(content) < 48 or len(content) > 256 or not content.isascii():
        raise PermissionError("invalid canary credential length")
    return content


def _check_signer() -> None:
    name = "system.matemail.pro"
    key_material = dkim.public_material_of(dkim.key_path(name, "mm1"))
    if not key_material:
        raise PermissionError("system DKIM key is not available")
    with db.connect_runtime() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public_key FROM public.dkim_key "
                "WHERE domain_name=%s AND selector='mm1' AND active=true",
                (name,),
            )
            row = cur.fetchone()
    if not row or key_material != row[0]:
        raise PermissionError("system DKIM signing key does not match the engine record")


def activate_operations_canary(body: dict) -> dict:
    if not _allowed("NATIVE_SYSTEM_CANARY_ACTIVATION_ENABLED"):
        raise PermissionError("operations canary activation disabled")
    if not isinstance(body, dict) or body != ACTIVATE_BODY:
        raise ValidationError("only operations canary activation is supported")
    _check_signer()
    secret = _read_canary_secret()
    hashed = passwords.hash_password(secret)
    del secret
    with db.connect_platform() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT public.platform_activate_system_canary(%s)", (hashed,))
            result = cur.fetchone()[0]
        conn.commit()
    return result


def deactivate_operations_canary(body: dict) -> dict:
    if not _allowed("NATIVE_SYSTEM_CANARY_ROLLBACK_ENABLED"):
        raise PermissionError("operations canary rollback disabled")
    if not isinstance(body, dict) or body != ROLLBACK_BODY:
        raise ValidationError("only operations canary rollback is supported")
    with db.connect_platform() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT public.platform_deactivate_system_canary()")
            result = cur.fetchone()[0]
        conn.commit()
    return result
