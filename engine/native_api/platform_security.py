"""Dedicated, feature-gated platform-only inactive staging.

Existing Native API and Dovecot credentials are intentionally unusable here.
The database operation runs as an unprivileged dedicated PostgreSQL principal
that has no direct access to domain, mailbox or DKIM mutations.
"""
from __future__ import annotations

import hmac
import os

import db
from validation import ValidationError

SYSTEM_DOMAIN = "system.matemail.pro"
_STAGE_BODY = {"domain": SYSTEM_DOMAIN, "operation": "stage_inactive"}


def enabled() -> bool:
    return os.environ.get("NATIVE_PLATFORM_OPERATIONS_ENABLED", "False").strip().lower() == "true"


def credentials_distinct() -> bool:
    auth = os.environ.get("NATIVE_PLATFORM_SECRET", "")
    db_password = os.environ.get("NATIVE_PLATFORM_DB_PASSWORD", "")
    baseline = (
        os.environ.get("NATIVE_API_SECRET", ""),
        os.environ.get("NATIVE_DOVECOT_POLICY_SECRET", ""),
        os.environ.get("NATIVE_DB_PASSWORD", ""),
    )
    return (
        len(auth) >= 32 and len(db_password) >= 32 and
        auth != db_password and
        all(secret and auth != secret and db_password != secret for secret in baseline)
    )


def authorized(handler) -> bool:
    if not enabled() or not credentials_distinct():
        return False
    provided = handler.headers.get("X-Native-Platform-Secret", "")
    return hmac.compare_digest(
        provided.encode("utf-8"),
        os.environ["NATIVE_PLATFORM_SECRET"].encode("utf-8"),
    )


def stage_inactive_system_mail(body):
    """Exact command allowlist. No caller-chosen users, passwords or quotas."""
    if not enabled() or not credentials_distinct():
        raise PermissionError("platform staging is disabled")
    if not isinstance(body, dict) or body != _STAGE_BODY:
        raise ValidationError("only inactive system mail staging is supported")
    with db.connect_platform() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT public.platform_stage_system_mail()")
            result = cur.fetchone()[0]
        conn.commit()
    return result
