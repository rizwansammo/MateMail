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
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional

from .adapter import MailEngineAdapter
from .dto import (
    AliasSpec,
    RateLimit,
    DkimKeyInfo,
    DomainSpec,
    EngineDomain,
    EngineHealth,
    EngineMailbox,
    ForwardingSpec,
    MailboxSpec,
    MailboxUsage,
)
from .errors import EngineCapabilityMissing, Rejected

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
        self._rate_limits: dict[str, RateLimit] = {}

    # ── Domains ─────────────────────────────────────────────────────────────

    def ensure_domain(self, spec: DomainSpec) -> None:
        self._domains[spec.name] = spec
        # The real engine mints the DKIM keypair as part of creating a domain,
        # honouring the selector and key size sent on that same call. The stub
        # mirrors it so the shared contract suite exercises the lifecycle both
        # adapters actually have — and so provisioning code that relies on the
        # key already existing is tested, not just hoped for.
        #
        # Only on first creation: an ensure_domain against an existing domain is
        # a reconcile, and the engine does not re-key a domain it already holds.
        if spec.name not in self._dkim:
            self._dkim[spec.name] = self._mint_dkim(spec.name, spec.dkim_selector)
        logger.debug("[stub] ensure_domain %s", spec.name)

    def set_domain_active(self, domain_name: str, active: bool) -> None:
        existing = self._domains.get(domain_name)
        if existing is not None:
            # `replace` rather than rebuilding field by field. The hand-written
            # version silently dropped every field it did not mention, so each
            # new field on DomainSpec reset to its default here — which for the
            # storage numbers could turn a valid domain into one whose ceiling
            # exceeds its own pool just by toggling `active`.
            self._domains[domain_name] = replace(existing, active=active)

    def delete_domain(self, domain_name: str) -> None:
        # Idempotent: absent is an acceptable end state.
        self._domains.pop(domain_name, None)
        for address in [a for a in self._mailboxes if a.endswith(f"@{domain_name}")]:
            self._mailboxes.pop(address, None)
            self._passwords.pop(address, None)
        # The DKIM key is deliberately NOT removed here. The real engine keeps
        # it after the domain is gone — measured in P4B — and a stub that
        # cleaned up tidily would make the deprovisioning tests pass while
        # production leaked a signing key to the domain's next owner. The stub
        # keeps the hazard so the tests have something to catch. Callers must
        # use delete_dkim_key() explicitly.

    def list_domains(self) -> list[EngineDomain]:
        return [EngineDomain(name=s.name, active=s.active) for s in self._domains.values()]

    # ── Mailboxes ───────────────────────────────────────────────────────────

    def ensure_mailbox(self, spec: MailboxSpec, password: str = "") -> None:
        existing_password = self._passwords.get(spec.address, False)
        if spec.login_enabled and not password and not existing_password:
            raise Rejected(
                "a login-enabled mailbox requires a password",
                operation="ensure_mailbox",
            )
        self._mailboxes[spec.address] = spec
        if spec.login_enabled:
            if password:
                self._passwords[spec.address] = True
        else:
            self._passwords.pop(spec.address, None)
        logger.debug("[stub] ensure_mailbox %s", spec.address)

    def set_mailbox_active(self, address: str, active: bool) -> None:
        existing = self._mailboxes.get(address)
        if existing is not None:
            self._mailboxes[address] = replace(existing, active=active)

    def set_mailbox_password(self, address: str, password: str) -> None:
        if not password:
            raise Rejected("empty password", operation="set_mailbox_password")
        existing = self._mailboxes.get(address)
        if existing is not None and not existing.login_enabled:
            raise Rejected(
                "direct login is disabled for this mailbox",
                operation="set_mailbox_password",
            )
        self._passwords[address] = True

    def set_mailbox_quota(self, address: str, quota_mb: int) -> None:
        existing = self._mailboxes.get(address)
        if existing is not None:
            self._mailboxes[address] = replace(existing, quota_mb=quota_mb)

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

    # ── Outbound rate limits ────────────────────────────────────────────────

    def set_mailbox_rate_limit(self, address: str, limit: RateLimit) -> None:
        if limit.is_unlimited:
            # Mirrors the real engine: zero removes the limit rather than
            # storing one of zero, so a later read reports "none set".
            self._rate_limits.pop(address, None)
            return
        self._rate_limits[address] = limit

    def get_mailbox_rate_limit(self, address: str) -> Optional[RateLimit]:
        return self._rate_limits.get(address)

    def clear_mailbox_rate_limit(self, address: str) -> None:
        self._rate_limits.pop(address, None)

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

    def _mint_dkim(self, domain_name: str, selector: str = "") -> DkimKeyInfo:
        """
        Generate fresh public material for a domain.

        A monotonic counter rather than a timestamp: two rotations can land in
        the same microsecond and would then be indistinguishable. No private key
        is generated or stored, because none may cross this boundary.
        """
        selector = selector or "mm1"
        self._dkim_rotations += 1
        public_key = f"STUBPUBLICKEY{self._dkim_rotations:06d}"
        return DkimKeyInfo(
            selector=selector,
            public_key=public_key,
            dns_record_name=f"{selector}._domainkey.{domain_name}",
            dns_record_value=f"v=DKIM1;k=rsa;p={public_key}",
        )

    def get_dkim_public_key(self, domain_name: str) -> Optional[DkimKeyInfo]:
        return self._dkim.get(domain_name)

    def delete_dkim_key(self, domain_name: str) -> None:
        # Idempotent: no key is the desired end state.
        self._dkim.pop(domain_name, None)

    def rotate_dkim_key(self, domain_name: str, selector: str = "") -> DkimKeyInfo:
        # Mirrors the real adapter's delete-then-add sequence and its
        # non-idempotent result: every call yields new public material.
        existing = self._dkim.get(domain_name)
        selector = selector or (existing.selector if existing else "") or "mm1"
        self.delete_dkim_key(domain_name)
        info = self._mint_dkim(domain_name, selector)
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
        """
        Refused, matching the real engine.

        The stub exists so code can be written and tested without an engine. It
        is only useful if it refuses what the engine refuses — a stub that
        cheerfully succeeds here would let a feature be built, tested and
        merged against a capability mailcow 2026-07b does not have.
        """
        raise EngineCapabilityMissing(
            "no quarantine release endpoint at the pinned engine version",
            operation="release_quarantine_item",
        )

    # ── Health ──────────────────────────────────────────────────────────────

    def check_health(self) -> EngineHealth:
        return EngineHealth(reachable=True, detail="stub adapter")
