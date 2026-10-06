import logging

from django.db.models import Q

from apps.forwarding.models import ForwardingRule, ForwardingStatus
from apps.mail_engine.dto import ForwardGroupSpec
from apps.mail_engine.errors import MailEngineError
from apps.mail_engine.factory import get_adapter
from apps.mailboxes.models import Mailbox, MailboxKind, MailboxStatus

from .models import ForwardGroupSenderPolicy

logger = logging.getLogger(__name__)


class ForwardGroupLoop(ValueError):
    customer_message = (
        "That mailbox forwards back to this Forward Group and would create a delivery loop."
    )


def _active_member_mailboxes(group):
    return (
        Mailbox.objects
        .filter(
            forward_group_memberships__group=group,
            status=MailboxStatus.ACTIVE,
        )
        .distinct()
        .order_by("email")
    )


def resolved_destinations(group) -> tuple[str, ...]:
    return tuple(
        sorted(set(_active_member_mailboxes(group).values_list("email", flat=True)))
    )


def resolved_allowed_senders(group) -> tuple[str, ...]:
    policy = group.sender_policy
    if policy == ForwardGroupSenderPolicy.ANYONE:
        return ()

    if policy == ForwardGroupSenderPolicy.ORGANIZATION:
        qs = Mailbox.objects.filter(
            tenant=group.tenant,
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
        )
    elif policy == ForwardGroupSenderPolicy.MEMBERS:
        qs = _active_member_mailboxes(group).filter(kind=MailboxKind.PERSONAL)
    elif policy == ForwardGroupSenderPolicy.SELECTED:
        qs = Mailbox.objects.filter(
            forward_group_sender_grants__group=group,
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
        )
    else:
        qs = Mailbox.objects.none()

    return tuple(sorted(set(qs.values_list("email", flat=True))))


def spec_for(group) -> ForwardGroupSpec:
    destinations = resolved_destinations(group)
    return ForwardGroupSpec(
        address=group.address,
        domain=group.domain.domain,
        destinations=destinations,
        sender_policy=group.sender_policy,
        allowed_senders=resolved_allowed_senders(group),
        active=group.status == "active",
    )


def sync_group(group) -> ForwardGroupSpec:
    spec = spec_for(group)
    get_adapter().ensure_forward_group(spec)
    group.mail_engine_provisioned = True
    group.mail_engine_error = ""
    group.save(
        update_fields=["mail_engine_provisioned", "mail_engine_error", "updated_at"]
    )
    return spec


def mark_sync_failure(group, exc) -> None:
    if isinstance(exc, MailEngineError):
        message = exc.customer_message
        logger.error("Forward Group sync failed for %s: %s", group.address, exc.log_message)
    else:
        message = MailEngineError.customer_message
        logger.exception("Unexpected Forward Group sync failure for %s", group.address)

    group.mail_engine_error = message
    group.save(update_fields=["mail_engine_error", "updated_at"])


def assert_member_will_not_loop(group, mailbox) -> None:
    if ForwardingRule.objects.filter(
        source_mailbox=mailbox,
        destination_email__iexact=group.address,
        status=ForwardingStatus.ACTIVE,
    ).exists():
        raise ForwardGroupLoop()


def forwarding_would_loop(mailbox, destination_email: str) -> bool:
    from .models import ForwardGroup, ForwardGroupStatus

    return ForwardGroup.objects.filter(
        tenant=mailbox.tenant,
        address__iexact=destination_email,
        status=ForwardGroupStatus.ACTIVE,
        members__mailbox=mailbox,
    ).exists()


def sync_groups_affected_by_mailbox(mailbox, *, include_organization=True) -> None:
    """
    Best-effort reconciliation after mailbox lifecycle changes.

    Organization-only groups resolve to the set of active personal mailboxes,
    so adding/disabling/removing a mailbox changes their sender allowlist.
    Membership and selected-sender groups are also affected when that mailbox
    changes state.
    """
    from .models import ForwardGroup

    query = Q(members__mailbox=mailbox) | Q(allowed_senders__mailbox=mailbox)
    if include_organization:
        query |= Q(sender_policy=ForwardGroupSenderPolicy.ORGANIZATION)

    groups = (
        ForwardGroup.objects
        .filter(tenant=mailbox.tenant)
        .filter(query)
        .distinct()
    )
    for group in groups:
        try:
            sync_group(group)
        except Exception as exc:  # noqa: BLE001
            mark_sync_failure(group, exc)
