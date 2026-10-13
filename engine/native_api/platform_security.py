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


_DKIM_PREPARE_BODY = {"domain": SYSTEM_DOMAIN, "operation": "prepare_dkim_inactive"}


def dkim_preparation_enabled() -> bool:
    return (
        enabled() and credentials_distinct()
        and os.environ.get("NATIVE_PLATFORM_DKIM_PREPARE_ENABLED", "False").strip().lower() == "true"
    )


def prepare_inactive_system_dkim(body: dict) -> dict:
    """Generate exactly one key, with a separate operator-only SQL grant.

    The system domain and its five mailbox roles remain inactive. A crash after
    DB registration but before publishing the active key is handled on retry.
    Ordinary tenant DKIM provisioning never gains platform-domain authority.
    """
    import pathlib

    import dkim as dkim_lib
    import provisioning

    if not dkim_preparation_enabled():
        raise PermissionError("system DKIM preparation disabled")
    if not isinstance(body, dict) or body != _DKIM_PREPARE_BODY:
        raise ValidationError("only inactive system DKIM preparation is supported")

    with db.connect_platform() as conn:
        # Shared session lock blocks concurrent tenant rotations from altering
        # selectors.map while this operation reads and republishes it.
        with db.dkim_lifecycle_lock(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT public.platform_system_dkim_state()")
                state = cur.fetchone()[0]

            if state is None:
                material = dkim_lib.generate(SYSTEM_DOMAIN, "mm1")
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT public.platform_register_system_dkim(%s, %s)",
                            (material["public_key"], material["generation_path"]),
                        )
                        if cur.fetchone()[0] is not True:
                            raise RuntimeError("system DKIM registration was not confirmed")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    dkim_lib.remove(material["generation_path"])
                    raise
                state = {
                    "selector": "mm1",
                    "public_key": material["public_key"],
                    "generation_path": material["generation_path"],
                    "active": True,
                }

            if state.get("selector") != "mm1" or state.get("active") is not True:
                raise RuntimeError("system DKIM state requires operator review")
            path = pathlib.Path(state["generation_path"])
            expected_dir = dkim_lib.KEY_DIR.resolve()
            if path.parent.resolve() != expected_dir or path.name.startswith("."):
                raise RuntimeError("system DKIM generation location mismatch")
            expected_name_prefix = f"{SYSTEM_DOMAIN}.mm1.g"
            if not (path.name.startswith(expected_name_prefix) and
                    path.name.endswith(".key") and
                    len(path.name) == len(expected_name_prefix) + 16 + 4):
                raise RuntimeError("system DKIM generation identifier mismatch")

            active = dkim_lib.key_path(SYSTEM_DOMAIN, "mm1")
            material_public = dkim_lib.public_material_of(path)
            if material_public != state["public_key"]:
                raise RuntimeError("recorded system DKIM generation cannot be verified")
            live_public = dkim_lib.public_material_of(active)
            if live_public is None:
                # Valid generation was committed but activating its link failed.
                dkim_lib.activate(path, active)
                live_public = dkim_lib.public_material_of(active)
            if live_public != state["public_key"]:
                raise RuntimeError("unexpected live system DKIM key; no key rotation performed")

            # The standard Native lifecycle publisher is idempotent and
            # transactionally serialised by the same lock.
            with db.connect_runtime() as read_conn:
                provisioning._publish_selector_map(read_conn)

            record_name, record_value = dkim_lib.dns_record(
                SYSTEM_DOMAIN, "mm1", live_public
            )
            return {
                "domain": SYSTEM_DOMAIN,
                "state": "inactive",
                "selector": "mm1",
                "dns_record_name": record_name,
                "dns_record_value": record_value,
                "mail_delivery_activated": False,
                "credentials_created": False,
            }
