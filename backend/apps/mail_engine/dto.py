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
            # The plan's alias cap is workspace-wide, while the engine's is
            # per-domain, so a two-domain workspace could hold twice this many
            # in the engine. That is deliberate: MateMail's own check
            # (`check_alias_limit`) is the workspace-wide authority, and this is
            # a backstop that is never TIGHTER than the product limit. A
            # backstop tighter than the plan would reject aliases the customer
            # has paid for.
            kwargs["max_aliases"] = plan.max_aliases
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
    login_enabled: bool = True
    authorized_senders: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_model(cls, mailbox) -> "MailboxSpec":
        is_team_box = getattr(mailbox, "kind", "personal") == "team_box"
        authorized_senders: tuple[str, ...] = ()

        if getattr(mailbox, "pk", None):
            grant_type = "team_box" if is_team_box else "delegation"
            grants = (
                mailbox.access_grants_received
                .filter(
                    active=True,
                    grant_type=grant_type,
                    grantee_mailbox__kind="personal",
                    grantee_mailbox__status="active",
                    grantee_mailbox__mail_engine_provisioned=True,
                )
                .select_related("grantee_mailbox")
            )
            authorized_senders = tuple(sorted({
                grant.grantee_mailbox.email
                for grant in grants
                if grant.can_send_as or grant.can_send_on_behalf
            }))

        return cls(
            address=mailbox.email,
            local_part=mailbox.local_part,
            domain=mailbox.domain.domain,
            display_name=mailbox.full_name or "",
            quota_mb=mailbox.quota_mb,
            active=mailbox.status == "active",
            login_enabled=not is_team_box,
            authorized_senders=authorized_senders,
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
class ForwardGroupSpec:
    """
    A distribution address and its complete resolved delivery/sender policy.

    Forward Groups are not mailboxes and never gain a sending identity. The
    engine receives final destination and allowed-sender sets from MateMail and
    enforces them independently from Alias and Forwarding state.
    """

    address: str
    domain: str
    destinations: tuple[str, ...] = field(default_factory=tuple)
    sender_policy: str = "anyone"
    allowed_senders: tuple[str, ...] = field(default_factory=tuple)
    active: bool = True

    def __post_init__(self):
        if self.active and not self.destinations:
            raise ValueError("An active Forward Group requires at least one destination")
        if self.sender_policy not in {"anyone", "organization", "members", "selected"}:
            raise ValueError("Unsupported Forward Group sender policy")


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
class RateLimit:
    """
    An outbound sending limit the engine enforces on one mailbox.

    Engine-neutral on purpose: `messages` per `window`, where the window is one
    of a small fixed set every mail engine understands. The pinned engine
    happens to express this as a value plus a single-letter frame, but that is
    its vocabulary and it stops at the adapter — product code that had to know
    `rl_frame="h"` would be product code coupled to mailcow.

    `messages=0` means "no limit", matching how engines conventionally clear
    one. It is not the same as having no RateLimit at all, which means "we did
    not ask".
    """

    messages: int
    window: str = "hour"

    #: Windows the port accepts. Anything else is a caller error, not something
    #: to translate hopefully into whatever the engine might take.
    WINDOWS = ("second", "minute", "hour", "day")

    def __post_init__(self):
        if not isinstance(self.messages, int) or isinstance(self.messages, bool):
            raise ValueError(f"messages must be a whole number, got {self.messages!r}")
        if self.messages < 0:
            raise ValueError("messages cannot be negative")
        if self.window not in self.WINDOWS:
            raise ValueError(
                f"window must be one of {self.WINDOWS}, got {self.window!r}"
            )

    @property
    def is_unlimited(self) -> bool:
        return self.messages == 0


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
