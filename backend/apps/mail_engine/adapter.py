"""
The Mail Engine port.

This is the only boundary between MateMail and its internal Mail Engine. See
`docs/ARCHITECTURE.md` for the five boundary rules; the two that shape this file:

- **No engine-shaped data crosses outward.** Methods accept DTOs from `dto.py`
  and return `None` or DTOs. Engine payloads, IDs, hostnames and error text stop
  here.
- **Errors are typed.** Failures raise a `MailEngineError` subclass from
  `errors.py`. There is no result object carrying a status string, because a
  message field is exactly how engine text used to reach customers.

## Success and failure

A method that returns without raising has succeeded. There is no boolean to
check. Read operations return `None` or an empty collection when the engine
holds nothing, and raise only when the engine could not be consulted.

## Idempotency contract

Every mutating operation is safe to retry. This matters because provisioning
runs under Celery with automatic retries, and a partially-applied change must
converge rather than duplicate.

| Operation | Contract |
|---|---|
| `ensure_domain` | Upsert. Creates if absent, reconciles to the spec if present. Repeated calls with the same spec leave identical state and do not raise `AlreadyExists`. |
| `ensure_mailbox` | Upsert. Creates if absent; if present, updates display name, quota and active flag. A password is applied only when supplied, so a retry without one never clears a working credential. |
| `ensure_alias` | Upsert. The spec's destination set replaces whatever the engine held. |
| `ensure_forwarding` | Upsert. An empty destination set removes forwarding entirely, so "remove" and "set" are the same idempotent call. |
| `set_domain_active` / `set_mailbox_active` | Assignment, not toggle. Setting the current value is a no-op success. |
| `set_mailbox_rate_limit` / `clear_mailbox_rate_limit` | Assignment. Idempotent. |
| `set_mailbox_password` / `set_mailbox_quota` | Assignment. Naturally idempotent. |
| `delete_domain` / `delete_mailbox` / `delete_alias` | Idempotent. Deleting something already absent succeeds; it does **not** raise `NotFound`, because the caller's intent (it should not exist) is satisfied. |
| `delete_dkim_key` | Idempotent. An already-absent key is the desired end state. |
| `rotate_dkim_key` | **NOT idempotent.** Each call generates a new keypair and invalidates the previously published DNS record. Never call it from an automatic retry path. |
| `cancel_queue_message` / `release_quarantine_item` | Idempotent by message id; acting on an already-actioned message succeeds. |

`AlreadyExists` and `NotFound` therefore signal genuine conflicts — a colliding
object that is not the one we meant, or a reference that should have resolved —
not the ordinary outcome of a retry.

## DKIM

Per DEC-007r the engine owns the private key. This port exposes only
`get_dkim_public_key()`, `rotate_dkim_key()` and `delete_dkim_key()`. The first
two return public material; the third returns nothing. There is no method that
reads or writes a private key, and none may be added.

`delete_dkim_key` exists because of a property measured against the real engine
in P4B, not deduced: **removing a domain does not remove its DKIM key.** The key
outlives the domain, and a later re-registration of the same domain name adopts
the surviving key. In a multi-tenant product that means a new tenant can inherit
a previous tenant's private signing key, so deprovisioning must delete the key
explicitly. See `docs/MAIL_ENGINE.md` § "DKIM lifecycle".
"""
from abc import ABC, abstractmethod
from typing import Optional

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


class MailEngineAdapter(ABC):
    """
    Implementations: `StubAdapter` (local development/CI) and `NativeMailEngineAdapter` (production).

    Both must satisfy `tests/test_adapter_contract.py`. Add a method here only
    with a corresponding contract test, or the two implementations will drift.
    """

    # ── Domains ─────────────────────────────────────────────────────────────

    @abstractmethod
    def ensure_domain(self, spec: DomainSpec) -> None:
        """Create or reconcile a domain. Idempotent."""

    @abstractmethod
    def set_domain_active(self, domain_name: str, active: bool) -> None:
        """Enable or disable a domain. Idempotent assignment."""

    @abstractmethod
    def delete_domain(self, domain_name: str) -> None:
        """
        Remove a domain and everything it holds. Idempotent.

        DESTRUCTIVE: this discards stored mail for the domain. Callers must have
        their own confirmation and retention policy; the port does not add one.
        """

    @abstractmethod
    def list_domains(self) -> list[EngineDomain]:
        """Every domain the engine holds. For reconciliation, not customer display."""

    # ── Mailboxes ───────────────────────────────────────────────────────────

    @abstractmethod
    def ensure_mailbox(self, spec: MailboxSpec, password: str = "") -> None:
        """
        Create or reconcile a mailbox. Idempotent.

        `password` is applied only when non-empty, so a retry that omits it does
        not disturb a working credential. It is never stored by MateMail and
        never logged.
        """

    @abstractmethod
    def set_mailbox_active(self, address: str, active: bool) -> None:
        """Enable or disable a mailbox. Idempotent assignment."""

    @abstractmethod
    def set_mailbox_password(self, address: str, password: str) -> None:
        """Replace a mailbox password. Idempotent."""

    @abstractmethod
    def set_mailbox_quota(self, address: str, quota_mb: int) -> None:
        """Set a mailbox's storage quota. Idempotent."""

    @abstractmethod
    def delete_mailbox(self, address: str) -> None:
        """
        Remove a mailbox and its stored mail. Idempotent.

        DESTRUCTIVE — see `delete_domain`.
        """

    @abstractmethod
    def list_mailboxes(self, domain_name: str = "") -> list[EngineMailbox]:
        """Mailboxes the engine holds, optionally scoped to one domain."""

    @abstractmethod
    def set_mailbox_rate_limit(self, address: str, limit: "RateLimit") -> None:
        """
        Cap how much mail one mailbox may send. Idempotent assignment.

        This is the engine's own enforcement, applied at submission time. It is
        deliberately separate from MateMail's application-level limiter: the
        application limiter is consulted by the policy bridge and can be
        reasoned about in product terms, while this one holds even for a client
        that reaches the engine without passing through MateMail.

        Setting `messages=0` removes the limit.
        """

    @abstractmethod
    def get_mailbox_rate_limit(self, address: str) -> Optional["RateLimit"]:
        """The mailbox's current limit, or None if the engine has none set."""

    @abstractmethod
    def clear_mailbox_rate_limit(self, address: str) -> None:
        """Remove any limit. Idempotent — clearing an absent limit succeeds."""

    @abstractmethod
    def get_mailbox_usage(self, address: str) -> Optional[MailboxUsage]:
        """Storage consumed by a mailbox, or None if the engine has no record."""

    @abstractmethod
    def get_last_login(self, address: str) -> Optional[str]:
        """
        ISO-8601 timestamp of the mailbox's last successful login, or None.

        Returned as a string rather than a datetime so no engine-specific
        timestamp type crosses the boundary; the caller parses it.
        """

    # ── Aliases and forwarding ──────────────────────────────────────────────

    @abstractmethod
    def ensure_alias(self, spec: AliasSpec) -> None:
        """Create or reconcile an alias to the spec's destination set. Idempotent."""

    @abstractmethod
    def delete_alias(self, address: str) -> None:
        """Remove an alias. Idempotent."""

    @abstractmethod
    def ensure_forwarding(self, spec: ForwardingSpec) -> None:
        """
        Apply a mailbox's complete forwarding state. Idempotent.

        The spec's destination set is final and is applied verbatim. An empty
        set removes forwarding. The adapter must not decide which of MateMail's
        forwarding rules are active or whether to keep a copy — that is product
        logic and lives in `apps.forwarding.services`.
        """

    # ── DKIM (public material only — DEC-007r) ──────────────────────────────

    @abstractmethod
    def get_dkim_public_key(self, domain_name: str) -> Optional[DkimKeyInfo]:
        """Public DKIM material for DNS display, or None if the engine has no key."""

    @abstractmethod
    def rotate_dkim_key(self, domain_name: str, selector: str = "") -> DkimKeyInfo:
        """
        Have the engine generate a fresh keypair and return the public half.

        NOT idempotent: each call invalidates the currently published DNS
        record, so the customer must republish. Never call from a retry path.
        The private key is generated and retained inside the engine and must
        never be returned.

        Implementations must tolerate being called when the engine holds no key
        — that is the state a retry sees after a rotation failed partway — and
        must not destroy a replacement key they have just created.
        """

    @abstractmethod
    def delete_dkim_key(self, domain_name: str) -> None:
        """
        Remove the engine's DKIM keypair for a domain. Idempotent.

        Deleting a key the engine does not have succeeds: the caller's intent
        (no signing key must remain for this domain) is already satisfied.

        This is a security operation, not housekeeping. A key left behind after
        a domain is removed is inherited by whoever registers that domain name
        next — see the module docstring.
        """

    # ── Queue and quarantine ────────────────────────────────────────────────

    @abstractmethod
    def get_queue_status(self) -> list[dict]:
        """
        Engine-wide mail queue.

        NOT tenant-scoped — the engine has no tenant concept. Callers MUST map
        entries to a tenant before storing or displaying them, or this becomes a
        cross-tenant leak.
        """

    @abstractmethod
    def get_quarantine_items(self) -> list[dict]:
        """Engine-wide quarantine. Same tenant-scoping warning as `get_queue_status`."""

    @abstractmethod
    def cancel_queue_message(self, engine_message_id: str) -> None:
        """Drop a queued message. Idempotent by id."""

    @abstractmethod
    def release_quarantine_item(self, engine_message_id: str) -> None:
        """Deliver a quarantined message. Idempotent by id."""

    # ── Health ──────────────────────────────────────────────────────────────

    @abstractmethod
    def check_health(self) -> EngineHealth:
        """
        Operator-facing engine health. Never raises.

        Must not be exposed on a public endpoint: whether a distinct Mail Engine
        exists is itself infrastructure detail.
        """
