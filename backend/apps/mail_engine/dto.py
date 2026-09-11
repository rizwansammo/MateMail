"""
Data transfer objects for the Mail Engine boundary.

These are the *only* shapes allowed to cross the port. The adapter must not
receive Django model instances, and must not return engine response payloads.

Why: the port exists so the engine can be replaced. Passing ORM objects couples
the engine implementation to MateMail's schema, and returning engine payloads
lets engine-shaped data reach serializers. Both erode replaceability.

All DTOs are frozen — an adapter cannot mutate a caller's object, and a spec can
be safely reused across a retry.

Construction helpers (`from_model`) live here rather than on the models so that
`apps.mail_engine` stays the single place that knows what the engine needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ─── Inputs: instructions to the engine ──────────────────────────────────────


#: DKIM key size requested for new domains, in bits.
#:
#: Carried on the spec rather than hard-coded in an adapter because it is a
#: product decision (what MateMail publishes for its customers), not an engine
#: detail. 2048 is the interoperability sweet spot: 1024 is deprecated and
#: increasingly distrusted, while 4096 produces a TXT record that must be split
#: across strings and is mishandled by some DNS providers customers use.
DEFAULT_DKIM_KEY_SIZE = 2048


@dataclass(frozen=True)
class DomainSpec:
    """
    A domain's desired state in the engine.

    ## The three storage numbers

    The engine enforces all three at once and refuses a domain whose values
    contradict each other:

        default_quota_mb <= max_quota_mb <= total_quota_mb

    `total_quota_mb` is the pool shared by every mailbox on the domain. It is
    **carried, never derived.** Computing it as `max_mailboxes * max_quota_mb`
    inside the adapter would hard-code a policy that every mailbox may reach its
    ceiling simultaneously, which forecloses the shared-pool plans this field
    exists to express — 10 mailboxes, 5 GB each, 25 GB shared is a legitimate
    commercial shape, and the adapter has no business inventing or overriding
    it.

    Validated here rather than at the adapter, so an impossible plan fails
    before a request reaches the engine and the error is MateMail's own.
    """

    name: str
    dkim_selector: str = "mm1"
    dkim_key_size: int = DEFAULT_DKIM_KEY_SIZE
    max_mailboxes: int = 10
    max_aliases: int = 400
    default_quota_mb: int = 1024
    max_quota_mb: int = 1024
    total_quota_mb: int = 10240
    active: bool = True

    def __post_init__(self):
        from .errors import InvalidStorageConfiguration

        for label, value in (
            ("default_quota_mb", self.default_quota_mb),
            ("max_quota_mb", self.max_quota_mb),
            ("total_quota_mb", self.total_quota_mb),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise InvalidStorageConfiguration(
                    f"{label} must be a positive whole number of MB, got {value!r}",
                    operation="DomainSpec",
                )

        # Deliberately NOT clamped. Silently lowering a customer's ceiling to
        # fit a pool would make MateMail quietly sell less than the plan says;
        # an operator has to see the contradiction and decide which number is
        # wrong.
        if self.default_quota_mb > self.max_quota_mb:
            raise InvalidStorageConfiguration(
                f"default mailbox storage ({self.default_quota_mb} MB) exceeds the "
                f"per-mailbox maximum ({self.max_quota_mb} MB)",
                operation="DomainSpec",
            )
        if self.max_quota_mb > self.total_quota_mb:
            raise InvalidStorageConfiguration(
                f"per-mailbox maximum ({self.max_quota_mb} MB) exceeds the domain "
                f"total ({self.total_quota_mb} MB)",
                operation="DomainSpec",
            )

    @classmethod
    def from_model(cls, domain, *, plan=None) -> "DomainSpec":
        """
        Build a spec from a Domain model, optionally shaped by the tenant's plan.

        Plan-derived limits are deliberately resolved here in the product layer,
        not inside the adapter: what a plan permits is MateMail's business rule.
        """
        from django.conf import settings

        kwargs = {
            "name": domain.domain,
            # The deployment-wide selector is the fallback, not a literal. A
            # hard-coded "mm1" here would disagree with DKIM_SELECTOR the moment
            # anyone changed it, and the disagreement would surface as published
            # DNS that does not match what the engine signs with.
            "dkim_selector": domain.dkim_selector
            or getattr(settings, "DKIM_SELECTOR", "mm1"),
        }
        if plan is not None:
            kwargs["max_mailboxes"] = plan.max_mailboxes
            # All three come from the plan. Taking only the ceiling and leaving
            # the other two at dataclass defaults is what produced a plan with a
            # 512 MB ceiling and a 3072 MB default — an impossible domain the
            # engine rejected outright.
            kwargs["default_quota_mb"] = plan.default_storage_per_mailbox_mb
            kwargs["max_quota_mb"] = plan.max_storage_per_mailbox_mb
            kwargs["total_quota_mb"] = plan.max_storage_total_mb
        return cls(**kwargs)


@dataclass(frozen=True)
class MailboxSpec:
    """A mailbox's desired state in the engine."""

    address: str
    local_part: str
    domain: str
    display_name: str = ""
    quota_mb: int = 10240
    active: bool = True

    @classmethod
    def from_model(cls, mailbox) -> "MailboxSpec":
        return cls(
            address=mailbox.email,
            local_part=mailbox.local_part,
            domain=mailbox.domain.domain,
            display_name=mailbox.full_name or "",
            quota_mb=mailbox.quota_mb,
            active=mailbox.status == "active",
        )


@dataclass(frozen=True)
class AliasSpec:
    """
    An alias's desired state: one source address and its complete destination set.

    `destinations` is final. The adapter delivers exactly this set and does not
    compute, merge or filter it.
    """

    address: str
    destinations: tuple[str, ...] = field(default_factory=tuple)
    active: bool = True

    def __post_init__(self):
        if not self.destinations:
            raise ValueError("AliasSpec requires at least one destination")


@dataclass(frozen=True)
class ForwardingSpec:
    """
    The complete forwarding state for one mailbox.

    `destinations` is the final, fully-resolved delivery set — every active
    destination, plus the mailbox itself when a copy should be kept. Deciding
    what belongs in that set (which rules are active, whether to keep a copy) is
    MateMail product logic and lives in `apps.forwarding.services`.

    An empty `destinations` means "no forwarding" and instructs the adapter to
    remove any forwarding state for this mailbox.
    """

    mailbox_address: str
    destinations: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.destinations


# ─── Outputs: facts read back from the engine ────────────────────────────────


@dataclass(frozen=True)
class DkimKeyInfo:
    """
    Public DKIM material only.

    Per DEC-007r the engine generates and stores the private key; it never
    crosses this boundary. There is deliberately no private-key field here —
    adding one would reintroduce the very coupling DEC-007r removes.
    """

    selector: str
    public_key: str
    dns_record_name: str = ""
    dns_record_value: str = ""


@dataclass(frozen=True)
class MailboxUsage:
    """Storage actually consumed by a mailbox, as reported by the engine."""

    address: str
    used_mb: int
    quota_mb: int
    message_count: Optional[int] = None

    @property
    def percent_used(self) -> int:
        if not self.quota_mb:
            return 0
        return min(100, round(self.used_mb / self.quota_mb * 100))


@dataclass(frozen=True)
class EngineDomain:
    """A domain as the engine currently sees it. Used for reconciliation."""

    name: str
    active: bool = True


@dataclass(frozen=True)
class EngineMailbox:
    """A mailbox as the engine currently sees it. Used for reconciliation."""

    address: str
    active: bool = True
    quota_mb: int = 0


@dataclass(frozen=True)
class EngineHealth:
    """
    Operator-facing engine health. Never returned on a public endpoint.
    """

    reachable: bool
    detail: str = ""
