"""Stage 7A Native-only client credential lifecycle.

The Native API is the sole owner of these bcrypt hashes. Customer callers NEVER
talk to this module: request-level identity checks happen in PostBox before
the internal engine credential can be used. Protected platform identities are
not eligible. No password hash or plaintext input is returned or logged.
"""
from __future__ import annotations

import os
import secrets
import uuid

from validation import ValidationError, email_address
from passwords import hash_password, verify_password


def mutation_enabled() -> bool:
    return os.environ.get("NATIVE_CLIENT_CREDENTIAL_API_ENABLED", "").lower() == "true"


def web_verifier_enabled() -> bool:
    return os.environ.get("NATIVE_TRUSTED_WEB_AUTH_ENABLED", "").lower() == "true"


def _customer_mailbox(conn, address):
    normalized = email_address(address, "address")
    with conn.cursor() as cur:
        cur.execute(
            """SELECT m.id, m.password_hash
                 FROM public.mailbox m
                 JOIN public.domain d ON d.id=m.domain_id
                WHERE m.address=%s AND m.active AND m.login_enabled AND d.active
                  AND NOT EXISTS (
                    SELECT 1 FROM public.platform_reserved_domain rd
                    WHERE rd.name=d.name
                  )""", (normalized,),
        )
        row = cur.fetchone()
    if not row:
        # Do not distinguish protected addresses from unknown or inactive ones.
        raise ValidationError("Mailbox unavailable", "address")
    return row


def verify_primary(conn, address, password):
    """Use only behind the authenticated internal control API and app rate limits."""
    if not web_verifier_enabled():
        raise PermissionError("Trusted web authentication disabled")
    if not isinstance(password, str) or not password:
        return {"valid": False}
    try:
        _, stored = _customer_mailbox(conn, address)
    except ValidationError:
        return {"valid": False}
    return {"valid": verify_password(password, stored)}


def list_credentials(conn, address):
    if not mutation_enabled():
        raise PermissionError("Client credential lifecycle disabled")
    mailbox_id, _ = _customer_mailbox(conn, address)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id::text, label, created_at, revoked_at
                 FROM public.mail_client_password
                WHERE mailbox_id=%s ORDER BY created_at DESC""", (mailbox_id,),
        )
        items = cur.fetchall()
        cur.execute(
            """SELECT client_access_enabled, dedicated_password_required
                 FROM public.mail_client_policy WHERE mailbox_id=%s""", (mailbox_id,),
        )
        policy = cur.fetchone()
    return {
        "enabled": policy[0] if policy else True,
        "dedicated_required": policy[1] if policy else False,
        "credentials": [
            {"id": str(a), "label": b, "created_at": c.isoformat(),
             "revoked_at": d.isoformat() if d else None}
            for a,b,c,d in items
        ],
    }


def issue_credential(conn, address, label):
    if not mutation_enabled():
        raise PermissionError("Client credential lifecycle disabled")
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise ValidationError("Device label must be 1–80 characters", "label")
    mailbox_id, _ = _customer_mailbox(conn, address)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM public.mail_client_password
                WHERE mailbox_id=%s AND revoked_at IS NULL""", (mailbox_id,),
        )
        if cur.fetchone()[0] >= 10:
            raise ValidationError("Maximum active client passwords reached", "address")
        raw = secrets.token_urlsafe(32)
        digest = hash_password(raw)
        cur.execute(
            """INSERT INTO public.mail_client_password(mailbox_id,label,password_hash)
               VALUES (%s,%s,%s) RETURNING id::text""",
            (mailbox_id, label.strip(), digest),
        )
        identifier = cur.fetchone()[0]
    return {"id": identifier, "one_time_credential": raw}


def revoke_credential(conn, address, credential_id):
    if not mutation_enabled():
        raise PermissionError("Client credential lifecycle disabled")
    mailbox_id, _ = _customer_mailbox(conn, address)
    try:
        identifier = uuid.UUID(str(credential_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Invalid credential ID", "id") from None
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE public.mail_client_password SET revoked_at=now()
                WHERE mailbox_id=%s AND id=%s AND revoked_at IS NULL
                RETURNING id""", (mailbox_id, identifier),
        )
        revoked = cur.fetchone() is not None
    return {"revoked": revoked}


def set_policy(conn, address, enabled, dedicated_required):
    if not mutation_enabled():
        raise PermissionError("Client credential lifecycle disabled")
    if type(enabled) is not bool or type(dedicated_required) is not bool:
        raise ValidationError("Policy flags must be booleans")
    mailbox_id, _ = _customer_mailbox(conn, address)
    with conn.cursor() as cur:
        # Lock mailbox row to serialize changes with other engine lifecycle ops.
        cur.execute("SELECT id FROM public.mailbox WHERE id=%s FOR UPDATE", (mailbox_id,))
        if dedicated_required and enabled:
            cur.execute(
                """SELECT count(*) FROM public.mail_client_password
                    WHERE mailbox_id=%s AND revoked_at IS NULL""", (mailbox_id,),
            )
            if cur.fetchone()[0] == 0:
                raise ValidationError("Create a dedicated password before requiring it")
        cur.execute(
            """INSERT INTO public.mail_client_policy
                    (mailbox_id,client_access_enabled,dedicated_password_required)
               VALUES (%s,%s,%s)
               ON CONFLICT (mailbox_id) DO UPDATE
                 SET client_access_enabled=EXCLUDED.client_access_enabled,
                     dedicated_password_required=EXCLUDED.dedicated_password_required,
                     updated_at=now()""",
            (mailbox_id, enabled, dedicated_required),
        )
    return {"enabled": enabled, "dedicated_required": dedicated_required}
