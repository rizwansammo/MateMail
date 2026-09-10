"""
Mail Engine adapter — in-memory stub.

Default implementation, used for local development and CI so neither requires a
running Mail Engine. It is not a no-op: it keeps in-memory state so it can
honour the same idempotency and not-found semantics as the real adapter, which
is what makes the shared contract test suite meaningful.

State lives on the instance and is discarded when the process ends. Tests that
need a clean slate call `factory.reset_adapter()`.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from .adapter import MailEngineAdapter
from .dto import (
    AliasSpec,
    DkimKeyInfo,
    DomainSpec,
    EngineDomain,
    EngineHealth,
    EngineMailbox,
    ForwardingSpec,
    MailboxSpec,
    MailboxUsage,
)
from .errors import Rejected

logger = logging.getLogger(__name__)


class StubAdapter(MailEngineAdapter):
    def __init__(self):
        self._domains: dict[str, DomainSpec] = {}
        self._mailboxes: dict[str, MailboxSpec] = {}
        self._aliases: dict[str, AliasSpec] = {}
        self._dkim: dict[str, DkimKeyInfo] = {}
        # Passwords are held only to prove a retry does not clear a credential.
        # They are never returned, logged or persisted.
        self._passwords: dict[str, bool] = {}
        self._dkim_rotations = 0

    # ── Domains ─────────────────────────────────────────────────────────────

    def ensure_domain(self, spec: DomainSpec) -> None:
        self._domains[spec.name] = spec
        logger.debug("[stub] ensure_domain %s", spec.name)

    def set_domain_active(self, domain_name: str, active: bool) -> None:
        existing = self._domains.get(domain_name)
        if existing is not None:
            self._domains[domain_name] = DomainSpec(
                name=existing.name,
                dkim_selector=existing.dkim_selector,
                max_mailboxes=existing.max_mailboxes,
                max_aliases=existing.max_aliases,
                default_quota_mb=existing.default_quota_mb,
                max_quota_mb=existing.max_quota_mb,
                active=active,
            )

    def delete_domain(self, domain_name: str) -> None:
        # Idempotent: absent is an acceptable end state.
        self._domains.pop(domain_name, None)
        for address in [a for a in self._mailboxes if a.endswith(f"@{domain_name}")]:
            self._mailboxes.pop(address, None)
            self._passwords.pop(address, None)
        self._dkim.pop(domain_name, None)

    def list_domains(self) -> list[EngineDomain]:
        return [EngineDomain(name=s.name, active=s.active) for s in self._domains.values()]

    # ── Mailboxes ───────────────────────────────────────────────────────────

    def ensure_mailbox(self, spec: MailboxSpec, password: str = "") -> None:
        self._mailboxes[spec.address] = spec
        if password:
            self._passwords[spec.address] = True
        logger.debug("[stub] ensure_mailbox %s", spec.address)

    def set_mailbox_active(self, address: str, active: bool) -> None:
        existing = self._mailboxes.get(address)
        if existing is not None:
            self._mailboxes[address] = MailboxSpec(
                address=existing.address,
                local_part=existing.local_part,
                domain=existing.domain,
                display_name=existing.display_name,
                quota_mb=existing.quota_mb,
                active=active,
            )

    def set_mailbox_password(self, address: str, password: str) -> None:
        if not password:
            raise Rejected("empty password", operation="set_mailbox_password")
        self._passwords[address] = True

    def set_mailbox_quota(self, address: str, quota_mb: int) -> None:
        existing = self._mailboxes.get(address)
        if existing is not None:
            self._mailboxes[address] = MailboxSpec(
                address=existing.address,
                local_part=existing.local_part,
                domain=existing.domain,
                display_name=existing.display_name,
                quota_mb=quota_mb,
                active=existing.active,
            )

    def delete_mailbox(self, address: str) -> None:
        self._mailboxes.pop(address, None)
        self._passwords.pop(address, None)

    def list_mailboxes(self, domain_name: str = "") -> list[EngineMailbox]:
        return [
            EngineMailbox(address=s.address, active=s.active, quota_mb=s.quota_mb)
            for s in self._mailboxes.values()
            if not domain_name or s.domain == domain_name
        ]

    def get_mailbox_usage(self, address: str) -> Optional[MailboxUsage]:
        spec = self._mailboxes.get(address)
        if spec is None:
            return None
        return MailboxUsage(
            address=address, used_mb=0, quota_mb=spec.quota_mb, message_count=0
        )

    def get_last_login(self, address: str) -> Optional[str]:
        if address not in self._mailboxes:
            return None
        return datetime.now(timezone.utc).isoformat()

    # ── Aliases and forwarding ──────────────────────────────────────────────

    def ensure_alias(self, spec: AliasSpec) -> None:
        self._aliases[spec.address] = spec

    def delete_alias(self, address: str) -> None:
        self._aliases.pop(address, None)

    def ensure_forwarding(self, spec: ForwardingSpec) -> None:
        if spec.is_empty:
            self._aliases.pop(spec.mailbox_address, None)
            return
        self._aliases[spec.mailbox_address] = AliasSpec(
            address=spec.mailbox_address, destinations=spec.destinations, active=True
        )

    # ── DKIM — public material only (DEC-007r) ──────────────────────────────

    def get_dkim_public_key(self, domain_name: str) -> Optional[DkimKeyInfo]:
        return self._dkim.get(domain_name)

    def rotate_dkim_key(self, domain_name: str, selector: str = "") -> DkimKeyInfo:
        selector = selector or "mm1"
        # A new opaque public value each call, mirroring the real adapter's
        # non-idempotent behaviour. A monotonic counter rather than a timestamp:
        # two rotations can land in the same microsecond. No private key is
        # generated or stored, because none may cross this boundary.
        self._dkim_rotations += 1
        public_key = f"STUBPUBLICKEY{self._dkim_rotations:06d}"
        info = DkimKeyInfo(
            selector=selector,
            public_key=public_key,
            dns_record_name=f"{selector}._domainkey.{domain_name}",
            dns_record_value=f"v=DKIM1;k=rsa;p={public_key}",
        )
        self._dkim[domain_name] = info
        return info

    # ── Queue and quarantine ────────────────────────────────────────────────

    def get_queue_status(self) -> list[dict]:
        return []

    def get_quarantine_items(self) -> list[dict]:
        return []

    def cancel_queue_message(self, engine_message_id: str) -> None:
        logger.debug("[stub] cancel_queue_message %s", engine_message_id)

    def release_quarantine_item(self, engine_message_id: str) -> None:
        logger.debug("[stub] release_quarantine_item %s", engine_message_id)

    # ── Health ──────────────────────────────────────────────────────────────

    def check_health(self) -> EngineHealth:
        return EngineHealth(reachable=True, detail="stub adapter")
