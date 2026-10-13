"""Read-only system mail readiness checks scoped to platform-owned resources.

An engine API reporting resources does not prove SMTP delivery, DNS routing,
TLS policy, authentication or DKIM signing. No readiness result grants the
right to activate the draft or mutates any engine/DNS/credential resource.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from apps.domains.models import MailDomainOwnerKind, MailDomainOwnership
from apps.mail_engine.factory import get_adapter

from .models import PlatformSystemMailPointer
from .system_mail import ROLE_LOCAL_PARTS, SYSTEM_MAIL_DOMAIN, operational_configuration

logger = logging.getLogger(__name__)
LEGACY_DOMAIN = "mail.matemail.pro"


def _platform_claim(name: str) -> bool:
    return MailDomainOwnership.objects.filter(
        domain=name,
        owner_kind=MailDomainOwnerKind.PLATFORM,
        tenant__isnull=True,
        source_domain_id__isnull=True,
    ).exists()


def inspect_engine(*, adapter=None):
    """Use only adapter list methods; never call ensure, rotate or delete."""
    if getattr(settings, "MAIL_ENGINE_ADAPTER", "stub").strip().lower() != "native":
        return {
            "status": "unknown",
            "reason": "Native Engine is not configured; stub results are not proof.",
            "legacy_domain": "unknown",
            "target_domain": "unknown",
            "legacy_sender": "unknown",
            "target_roles": {role: "unknown" for role in ROLE_LOCAL_PARTS},
        }

    try:
        instance = adapter if adapter is not None else get_adapter()
        domains = {entry.name.lower(): bool(entry.active)
                   for entry in instance.list_domains()}
        legacy_addresses = {m.address.lower(): bool(m.active)
                            for m in instance.list_mailboxes(LEGACY_DOMAIN)}
        target_addresses = {m.address.lower(): bool(m.active)
                            for m in instance.list_mailboxes(SYSTEM_MAIL_DOMAIN)}
    except Exception as exc:
        # Never expose a native API URL, bearer secret or vendor exception text.
        logger.warning("System mail read-only engine inspection failed: %s", type(exc).__name__)
        return {
            "status": "unknown",
            "reason": "The Native Engine could not be inspected.",
            "legacy_domain": "unknown",
            "target_domain": "unknown",
            "legacy_sender": "unknown",
            "target_roles": {role: "unknown" for role in ROLE_LOCAL_PARTS},
        }

    return {
        "status": "observed",
        "reason": "Engine resource inventory only, not a delivery or DKIM test.",
        "legacy_domain": "active" if domains.get(LEGACY_DOMAIN) else
            ("inactive" if LEGACY_DOMAIN in domains else "absent"),
        "target_domain": "active" if domains.get(SYSTEM_MAIL_DOMAIN) else
            ("inactive" if SYSTEM_MAIL_DOMAIN in domains else "absent"),
        "legacy_sender": "active" if legacy_addresses.get(f"noreply@{LEGACY_DOMAIN}") else
            ("inactive" if f"noreply@{LEGACY_DOMAIN}" in legacy_addresses else "absent"),
        "target_roles": {
            role: "active" if target_addresses.get(f"{local}@{SYSTEM_MAIL_DOMAIN}") else
                ("inactive" if f"{local}@{SYSTEM_MAIL_DOMAIN}" in target_addresses else "absent")
            for role, local in ROLE_LOCAL_PARTS.items()
        },
    }



def _runtime_role(address: str, local: str) -> str:
    """Classify configured identity, not live delivery or authentication."""
    from email.utils import parseaddr

    parsed = parseaddr(address or "")[1].strip().lower()
    if not parsed:
        return "unset"
    if parsed == f"{local}@{LEGACY_DOMAIN}":
        return "legacy"
    if parsed == f"{local}@{SYSTEM_MAIL_DOMAIN}":
        return "target"
    return "other"


def runtime_alignment(operational: dict) -> dict:
    """Compare operational environment with the plan, independently of drafts."""
    roles = {
        "sender": _runtime_role(operational["sender"], "noreply"),
        "dmarc_receiver": _runtime_role(operational["dmarc_receiver"], "dmarc"),
        "tlsrpt_receiver": _runtime_role(operational["tlsrpt_receiver"], "tlsrpt"),
    }
    return {
        "roles": roles,
        "legacy_configuration_intact": all(value == "legacy" for value in roles.values()),
        "migration_started": any(value == "target" for value in roles.values()),
        "note": "Configuration identity only; delivery, DNS and ingestion have not been verified.",
    }


def planned_engine_operations(engine: dict) -> dict:
    """Explain work needed later; never call any mutation API."""
    if engine["status"] != "observed":
        return {
            "domain": "unknown",
            "mailboxes": {role: "unknown" for role in ROLE_LOCAL_PARTS},
            "changes_applied": False,
        }
    status_action = {
        "active": "present",
        "inactive": "would_need_activation",
        "absent": "would_need_provisioning",
    }
    return {
        "domain": status_action[engine["target_domain"]],
        "mailboxes": {
            role: status_action[status] for role, status in engine["target_roles"].items()
        },
        "changes_applied": False,
    }


def system_mail_readiness(*, adapter=None):
    pointer = PlatformSystemMailPointer.objects.select_related("draft").filter(pk=1).first()
    claim_ok = _platform_claim(SYSTEM_MAIL_DOMAIN)
    legacy_claim_ok = _platform_claim(LEGACY_DOMAIN)
    engine = inspect_engine(adapter=adapter)
    operational = operational_configuration()
    return {
        "checked_at": timezone.now().isoformat(),
        "domain": SYSTEM_MAIL_DOMAIN,
        "revision": pointer.revision if pointer else 0,
        "draft_present": bool(pointer and pointer.draft_id),
        "platform_ownership": {
            "target": "verified" if claim_ok else "blocked",
            "legacy": "verified" if legacy_claim_ok else "blocked",
        },
        "engine": engine,
        "operational": operational,
        "runtime_alignment": runtime_alignment(operational),
        "provisioning_preview": planned_engine_operations(engine),
        "outstanding_gates": {
            "dns": "not_verified",
            "dkim_signing": "not_verified",
            "smtp_delivery": "not_tested",
            "imap_access": "not_tested",
            "new_credentials": "not_provisioned",
            "rollback_release": "not_approved",
        },
        "activation_supported": False,
        "cutover_ready": False,
        "note": (
            "This is a read-only inventory. Nothing here authorizes system "
            "domain provisioning, DNS changes, sender switching or deployment."
        ),
    }
