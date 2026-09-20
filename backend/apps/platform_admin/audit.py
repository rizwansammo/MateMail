"""
Writing the platform audit trail.

One writer, so the rules about what may be recorded are stated once.

WHY FAILURES ARE NOT SWALLOWED
    `apps.logs.utils.log_event` ends in `except Exception: pass`. For a mail
    event that is a defensible trade — a delivery should not 500 because a log
    row would not insert. It is the wrong trade here. These rows describe who
    suspended an organization and who triggered a customer's password
    recovery, and an audit trail that quietly loses the interesting rows is
    worse than none, because its silence reads as "nothing happened".

    So this raises. A platform action that cannot be recorded does not happen.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Keys that must never reach `metadata`, checked rather than trusted. Every
#: caller today is careful; this is what keeps that true after the next one.
_FORBIDDEN_METADATA = {
    "password", "new_password", "code", "raw_code", "token", "raw_token",
    "challenge", "secret", "totp_secret", "api_key", "password_hash",
    "dkim_private_key", "private_key",
}


def record_platform_action(
    *,
    actor,
    action: str,
    request=None,
    tenant=None,
    target_user=None,
    target_label: str = "",
    result: str = "success",
    reason: str = "",
    metadata: dict | None = None,
):
    """
    Append one row to the platform audit log and return it.

    Raises if the row cannot be written, and raises if a caller tries to record
    something secret.
    """
    from .models import PlatformAuditLog

    payload = dict(metadata or {})
    leaked = _FORBIDDEN_METADATA & {k.lower() for k in payload}
    if leaked:
        raise ValueError(
            f"refusing to write secret-looking keys to the audit log: {sorted(leaked)}"
        )

    ip_address = None
    if request is not None:
        # Only the hops our own infrastructure added. An audit row carrying an
        # attacker-chosen X-Forwarded-For is worse than one carrying none,
        # because it reads as evidence.
        from apps.security.client_ip import get_client_ip

        ip_address = get_client_ip(request)

    return PlatformAuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_email=getattr(actor, "email", "") or "",
        action=action,
        tenant=tenant,
        target_user=target_user,
        target_label=target_label or "",
        result=result,
        reason=reason or "",
        ip_address=ip_address,
        metadata=payload,
    )
