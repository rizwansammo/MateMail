"""
Forwarding product logic.

This resolves *what* a mailbox's forwarding should be. The Mail Engine adapter
only carries out the resulting instruction.

from apps.mail_engine.dto import ForwardingSpec

from .models import ForwardingRule, ForwardingStatus

logger = logging.getLogger(__name__)


def resolve_forwarding_spec(mailbox, *, excluding_rule_pk=None) -> ForwardingSpec:
    """
    Build the complete forwarding instruction for `mailbox`.

    Product rules applied here:

    - Only ACTIVE rules contribute a destination. Paused or disabled rules are
      ignored without being deleted.
    - If any active rule sets `keep_copy`, the mailbox's own address joins the
      destination set so a copy is still delivered locally.
    - Destinations are de-duplicated and ordered deterministically, so an
      unchanged rule set always produces an identical spec. That is what makes
      re-applying safe.
    - An empty destination set is a valid instruction meaning "no forwarding",
      which the adapter implements by removing forwarding state.

    `excluding_rule_pk` lets a caller compute the state that *will* apply once a
    rule is removed, without relying on the delete having been committed first.
    """
    rules = ForwardingRule.objects.filter(
        source_mailbox=mailbox, status=ForwardingStatus.ACTIVE
    )
    if excluding_rule_pk is not None:
        rules = rules.exclude(pk=excluding_rule_pk)

    active = list(rules)
    if not active:
        return ForwardingSpec(mailbox_address=mailbox.email, destinations=())

    destinations = {rule.destination_email for rule in active}
    if any(rule.keep_copy for rule in active):
        destinations.add(mailbox.email)

    return ForwardingSpec(
        mailbox_address=mailbox.email,
        destinations=tuple(sorted(destinations)),
    )


def apply_forwarding(mailbox, *, excluding_rule_pk=None) -> ForwardingSpec:
    """
    Resolve and push the mailbox's forwarding state to the Mail Engine.

    Raises the adapter's typed errors on failure; callers decide how to surface
    them. Returns the spec that was applied, for logging and assertions.
    """
    from apps.mail_engine.factory import get_adapter

    spec = resolve_forwarding_spec(mailbox, excluding_rule_pk=excluding_rule_pk)
    get_adapter().ensure_forwarding(spec)
    logger.info(
        "Applied forwarding for %s: %d destination(s)", mailbox.email, len(spec.destinations)
    )
    return spec
