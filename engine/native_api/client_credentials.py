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

from validation import ValidationError, email_address, domain_name
from passwords import hash_password, verify_password

# An unknown, suspended, or platform-reserved mailbox must consume a bcrypt
# verification just like a real mailbox. The hash is a disposable, non-login
# decoy generated once per API process; it is NOT an authentication credential.
# The decoy verification result is NEVER used to grant access.
_UNKNOWN_MAILBOX_HASH = hash_password("matemail-nonloginable-timing-decoy")


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
        # Keep negative responses generic AND similar in timing to a valid
        # bcrypt check. No early return that becomes a mailbox-existence oracle.
        verify_password(password, _UNKNOWN_MAILBOX_HASH)
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
        # Serialize the active-password cap with other creates/revokes and
        # policy transitions for this ONE mailbox (no global lock).
        cur.execute("SELECT id FROM public.mailbox WHERE id=%s FOR UPDATE",
                    (mailbox_id,))
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
        # Also serialize revocation with a concurrent create / policy update.
        cur.execute("SELECT id FROM public.mailbox WHERE id=%s FOR UPDATE",
                    (mailbox_id,))
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


def set_tenant_required(conn, tenant_id, domains, required):
    """Atomically bind a tenant and reconcile its persistent protocol policy.

    Re-saving an unchanged policy must not disconnect healthy mail clients.
    On retry after a partial commit/kick outage, process only the durable
    outstanding disconnects, never re-kick the whole organization.
    """
    if not mutation_enabled():
        raise PermissionError("Native tenant MFA management disabled")
    if type(required) is not bool:
        raise ValidationError("required must be a boolean", "required")
    try:
        normalized_tenant = str(uuid.UUID(str(tenant_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("tenant_id must be a UUID", "tenant_id") from None
    if (not isinstance(domains, list) or len(domains) > 200
            or any(not isinstance(value, str) for value in domains)):
        raise ValidationError("domains must be a bounded list", "domains")
    normalized_domains = [domain_name(value) for value in domains]
    if len(set(normalized_domains)) != len(normalized_domains):
        raise ValidationError("Duplicate domain names", "domains")

    with conn.cursor() as cur:
        cur.execute(
            """SELECT d.name, d.tenant_id::text, (rd.name IS NOT NULL)
                 FROM public.domain d
                 LEFT JOIN public.platform_reserved_domain rd ON rd.name=d.name
                 WHERE d.name = ANY(%s) ORDER BY d.name FOR UPDATE OF d""",
            (normalized_domains,),
        )
        found = cur.fetchall()
        if len(found) != len(normalized_domains):
            raise ValidationError("Some tenant domains are not provisioned", "domains")
        if any(is_platform for _, _, is_platform in found):
            raise ValidationError("Protected platform domain", "domains")
        if any(owner is not None and owner != normalized_tenant
               for _, owner, _ in found):
            raise ValidationError("Domain belongs to another tenant", "domains")

        cur.execute(
            "SELECT name FROM public.domain WHERE tenant_id=%s::uuid",
            (normalized_tenant,),
        )
        bound = {row[0] for row in cur.fetchall()}
        if not bound.issubset(set(normalized_domains)):
            raise ValidationError("Tenant domain list is incomplete", "domains")

        cur.execute(
            """SELECT required FROM public.native_tenant_mfa_policy
               WHERE tenant_id=%s::uuid FOR UPDATE""",
            (normalized_tenant,),
        )
        previous = cur.fetchone()
        changed = (required if previous is None
                   else bool(previous[0]) != required)
        newly_bound = [name for name, owner, _ in found if owner is None]

        if normalized_domains:
            cur.execute(
                """UPDATE public.domain SET tenant_id=%s::uuid
                    WHERE name = ANY(%s)
                      AND (tenant_id IS NULL OR tenant_id=%s::uuid)""",
                (normalized_tenant, normalized_domains, normalized_tenant),
            )
            if cur.rowcount != len(normalized_domains):
                raise ValidationError("Domain binding changed concurrently", "domains")

        cur.execute(
            """INSERT INTO public.native_tenant_mfa_policy(tenant_id, required)
               VALUES (%s::uuid, %s)
               ON CONFLICT (tenant_id) DO UPDATE SET
                 required=EXCLUDED.required, updated_at=now()""",
            (normalized_tenant, required),
        )

        # Only an actual transition or newly protected, previously unmapped
        # domains requires disconnecting currently authenticated clients.
        changed_addresses = []
        if changed or (required and newly_bound):
            cur.execute(
                """SELECT m.address FROM public.mailbox m
                   JOIN public.domain d ON d.id=m.domain_id
                   WHERE d.tenant_id=%s::uuid AND m.login_enabled
                     AND (%s OR d.name = ANY(%s))
                   ORDER BY m.address""",
                (normalized_tenant, changed, newly_bound),
            )
            changed_addresses = [row[0] for row in cur.fetchall()]

        # A retry after an HTTP 503 must reconcile just the previously queued
        # failures; returning success while pending disconnects exist is unsafe.
        cur.execute(
            """SELECT m.address FROM public.mail_client_kick_pending k
               JOIN public.mailbox m ON m.id=k.mailbox_id
               JOIN public.domain d ON d.id=m.domain_id
               WHERE d.tenant_id=%s::uuid ORDER BY m.address""",
            (normalized_tenant,),
        )
        pending_addresses = [row[0] for row in cur.fetchall()]
        to_disconnect = sorted(set(changed_addresses) | set(pending_addresses))
    return {
        "required": required,
        "mailbox_count": len(to_disconnect),
        "_kick_addresses": to_disconnect,
    }
