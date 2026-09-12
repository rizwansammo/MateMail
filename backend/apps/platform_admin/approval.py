"""
Platform-admin approval and abuse controls for workspaces.

During the Private Beta MateMail is free and admin-approved (DEC-016), so
approval is a real gate rather than a policy someone remembers: a new workspace
starts in `PENDING_APPROVAL` and can provision nothing into the Mail Engine
until a human at MateMail says so.

Every action here is an explicit state transition with a recorded actor, time
and reason. Letting a workspace send mail affects the sending reputation every
other customer shares, so "who allowed this, and when" has to be answerable
months later.

These are service functions rather than view logic so the transitions can be
called from a management command or a future admin UI without duplicating the
rules — and so the audit write can never be forgotten by a second caller.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.logs.models import LogEventType
from apps.logs.utils import log_event
from apps.tenants.models import Tenant, TenantStatus

logger = logging.getLogger(__name__)

#: Statuses an approval may be granted from. Approving an already-active
#: workspace is a no-op the caller should be told about rather than a silent
#: success, and approving a cancelled one is almost certainly a mistake.
APPROVABLE_FROM = frozenset({
    TenantStatus.PENDING_APPROVAL,
    TenantStatus.REJECTED,
})


class ApprovalError(Exception):
    """The requested transition is not valid from the workspace's current state."""


def _locked(tenant_id) -> Tenant:
    """
    Re-read the workspace under a row lock.

    Two admins acting at once — one approving, one rejecting — must not
    interleave into a state where the status says one thing and the approval
    timestamp says another.
    """
    return Tenant.objects.select_for_update().get(pk=tenant_id)


@transaction.atomic
def approve_tenant(tenant_id, *, actor, reason: str = "") -> Tenant:
    """
    Allow a workspace to use the Mail Engine.

    `actor` is the platform admin performing the action — recorded on the
    workspace and in the audit log. It is required: an approval with no
    attributable human is not an approval.
    """
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApprovalError("An approval must be attributable to a signed-in platform admin.")

    tenant = _locked(tenant_id)

    if tenant.status not in APPROVABLE_FROM:
        raise ApprovalError(
            f"This workspace is {tenant.get_status_display().lower()} and cannot be approved "
            "from that state."
        )

    previous = tenant.status
    tenant.status = TenantStatus.TRIAL
    tenant.approved_at = timezone.now()
    tenant.approved_by = actor
    tenant.review_reason = reason or ""
    tenant.save(update_fields=[
        "status", "approved_at", "approved_by", "review_reason", "updated_at",
    ])

    logger.info(
        "Platform admin %s APPROVED tenant %s for mail (was %s)",
        actor.email, tenant.id, previous,
    )
    log_event(
        tenant, LogEventType.TENANT_APPROVED,
        source=actor.email,
        metadata={"from_status": previous, "to_status": tenant.status, "reason": reason},
    )
    return tenant


@transaction.atomic
def reject_tenant(tenant_id, *, actor, reason: str = "") -> Tenant:
    """
    Refuse a workspace. Reversible — `approve_tenant` accepts a rejected one.

    Rejection does not delete anything. The workspace and its data remain; it
    simply cannot provision mail. Deleting on rejection would make an
    appeal impossible and a mistake unrecoverable.
    """
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApprovalError("A rejection must be attributable to a signed-in platform admin.")

    tenant = _locked(tenant_id)

    if tenant.status == TenantStatus.REJECTED:
        raise ApprovalError("This workspace has already been rejected.")
    if tenant.status in (TenantStatus.CANCELLED,):
        raise ApprovalError("This workspace is closed.")

    previous = tenant.status
    tenant.status = TenantStatus.REJECTED
    # Approval is revoked, not merely overridden: `can_use_mail` requires the
    # timestamp, so clearing it means a later status change cannot accidentally
    # restore mail access without a fresh, explicit approval.
    tenant.approved_at = None
    tenant.approved_by = None
    tenant.review_reason = reason or ""
    tenant.save(update_fields=[
        "status", "approved_at", "approved_by", "review_reason", "updated_at",
    ])

    logger.warning(
        "Platform admin %s REJECTED tenant %s (was %s): %s",
        actor.email, tenant.id, previous, reason or "no reason given",
    )
    log_event(
        tenant, LogEventType.TENANT_REJECTED,
        source=actor.email,
        metadata={"from_status": previous, "to_status": tenant.status, "reason": reason},
    )
    return tenant


@transaction.atomic
def set_outbound_enabled(tenant_id, *, enabled: bool, actor, reason: str = "") -> Tenant:
    """
    Turn a workspace's outbound sending off or on without suspending it.

    The lighter of the two abuse responses, and the one to reach for first. A
    workspace with outbound disabled keeps receiving mail, keeps its data, and
    can still be administered — only sending stops. That makes it safe to apply
    quickly on suspicion and to reverse just as quickly if the suspicion was
    wrong, which is exactly what an abuse response needs.

    Full suspension is heavier and is `AdminTenantSuspendView`.
    """
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApprovalError("This action must be attributable to a signed-in platform admin.")

    tenant = _locked(tenant_id)

    if tenant.outbound_disabled == (not enabled):
        state = "disabled" if not enabled else "enabled"
        raise ApprovalError(f"Outbound mail is already {state} for this workspace.")

    tenant.outbound_disabled = not enabled
    tenant.outbound_disabled_at = None if enabled else timezone.now()
    if reason:
        tenant.review_reason = reason
    tenant.save(update_fields=[
        "outbound_disabled", "outbound_disabled_at", "review_reason", "updated_at",
    ])

    event = (
        LogEventType.TENANT_OUTBOUND_ENABLED if enabled
        else LogEventType.TENANT_OUTBOUND_DISABLED
    )
    logger.warning(
        "Platform admin %s %s outbound mail for tenant %s: %s",
        actor.email, "ENABLED" if enabled else "DISABLED", tenant.id,
        reason or "no reason given",
    )
    log_event(
        tenant, event,
        source=actor.email,
        metadata={"outbound_disabled": tenant.outbound_disabled, "reason": reason},
    )
    return tenant


def _apply_engine_state(tenant_id, *, active: bool) -> bool:
    """
    Queue the engine-side half of a suspension. Returns whether it was queued.

    Separated from the transition so the caller can tell the operator the truth:
    the database says suspended, and here is whether the engine has been told.
    A failure to queue is reported, never swallowed — an operator who believes a
    suspension took effect when it did not will stop looking at an abuse
    incident that is still running.
    """
    try:
        from apps.mail_engine.tasks import apply_tenant_suspension_task
        apply_tenant_suspension_task.delay(str(tenant_id), active)
        return True
    except Exception as exc:
        logger.error(
            "SECURITY: could not queue Mail Engine %s for tenant %s: %s. The "
            "workspace's domains may still be active in the engine.",
            "reactivation" if active else "suspension", tenant_id, exc,
        )
        return False


@transaction.atomic
def suspend_tenant(tenant_id, *, actor, reason: str = "") -> tuple[Tenant, bool]:
    """
    Suspend a workspace: no sending, no receiving, no provisioning.

    The heavier of the two abuse responses. Reach for `set_outbound_enabled`
    first — it stops the damage while leaving the customer able to receive mail
    and put their case. Suspension is for when the whole workspace has to stop.

    Returns `(tenant, engine_queued)`. The second value is not decoration: the
    caller must tell the operator whether the engine was actually told, because
    the database row alone does not stop a single message.

    Nothing is deleted. A suspension is reversible by design.
    """
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApprovalError("A suspension must be attributable to a signed-in platform admin.")

    tenant = _locked(tenant_id)

    if tenant.status == TenantStatus.SUSPENDED:
        raise ApprovalError("This workspace is already suspended.")
    if tenant.status == TenantStatus.CANCELLED:
        raise ApprovalError("This workspace is closed.")

    previous = tenant.status
    tenant.status = TenantStatus.SUSPENDED
    if reason:
        tenant.review_reason = reason
    # `approved_at` is deliberately KEPT. Suspension is not a withdrawal of
    # approval, and clearing it would force a fresh approval for what is often
    # a billing problem resolved the same afternoon. `can_use_mail` already
    # denies on status, so nothing is permitted by the timestamp surviving.
    tenant.save(update_fields=["status", "review_reason", "updated_at"])

    logger.warning(
        "Platform admin %s SUSPENDED tenant %s (was %s): %s",
        actor.email, tenant.id, previous, reason or "no reason given",
    )
    log_event(
        tenant, LogEventType.TENANT_SUSPENDED,
        source=actor.email,
        metadata={"from_status": previous, "reason": reason},
    )

    # Queued after the audit write, inside the transaction, so a rollback takes
    # the log entry with it and the two can never disagree.
    queued = _apply_engine_state(tenant.id, active=False)
    return tenant, queued


@transaction.atomic
def reactivate_tenant(tenant_id, *, actor, reason: str = "") -> tuple[Tenant, bool]:
    """
    Return a suspended workspace to service.

    Refuses a workspace that was never approved. Activating a pending or
    rejected workspace used to be allowed and set the status to ACTIVE, which
    looked like it worked and did nothing: `can_use_mail` also requires an
    approval, so the workspace still could not provision or send, with no
    explanation anywhere the operator would see. Approval is the right door,
    and this one now says so.
    """
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApprovalError("This action must be attributable to a signed-in platform admin.")

    tenant = _locked(tenant_id)

    if tenant.approved_at is None:
        raise ApprovalError(
            "This workspace has never been approved, so it cannot be reactivated. "
            "Approve it instead."
        )
    if tenant.status == TenantStatus.CANCELLED:
        raise ApprovalError("This workspace is closed.")

    previous = tenant.status
    tenant.status = TenantStatus.ACTIVE
    if reason:
        tenant.review_reason = reason
    tenant.save(update_fields=["status", "review_reason", "updated_at"])

    logger.info(
        "Platform admin %s REACTIVATED tenant %s (was %s)",
        actor.email, tenant.id, previous,
    )
    log_event(
        tenant, LogEventType.TENANT_REACTIVATED,
        source=actor.email,
        metadata={"from_status": previous, "reason": reason},
    )

    queued = _apply_engine_state(tenant.id, active=True)
    return tenant, queued


@transaction.atomic
def set_mailbox_suspended(mailbox_id, *, suspended: bool, actor, reason: str = ""):
    """
    Suspend or release a single mailbox, as a platform admin.

    The narrowest abuse response MateMail has: one compromised account stops
    sending while the rest of the workspace — which has usually done nothing
    wrong — keeps working. Reach for this before disabling a workspace's
    outbound, and long before suspending it.

    SUSPENDED is deliberately a state only MateMail can set or clear. A tenant
    admin's own control offers ACTIVE and DISABLED only, and
    `MailboxStatusView` refuses to change a suspended mailbox at all, so a
    customer cannot lift a platform suspension on the account MateMail has just
    stopped. That asymmetry is the entire point of having two states that both
    mean "not sending".

    The engine is updated FIRST and a failure aborts the whole thing. A
    database row saying `suspended` over a mailbox the engine will still accept
    submission from is the exact false-assurance this function exists to
    prevent — so the transaction rolls back and the operator sees an error
    rather than a suspension that did not happen.
    """
    from apps.mail_engine.errors import MailEngineError
    from apps.mail_engine.factory import get_adapter
    from apps.mailboxes.models import Mailbox, MailboxStatus

    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApprovalError("This action must be attributable to a signed-in platform admin.")

    mailbox = (
        Mailbox.objects.select_for_update()
        .select_related("tenant", "domain")
        .get(pk=mailbox_id)
    )

    is_suspended = mailbox.status == MailboxStatus.SUSPENDED
    if is_suspended == suspended:
        state = "suspended" if suspended else "not suspended"
        raise ApprovalError(f"This mailbox is already {state}.")

    if mailbox.mail_engine_provisioned:
        try:
            get_adapter().set_mailbox_active(mailbox.email, not suspended)
        except MailEngineError as exc:
            logger.error(
                "Mail Engine refused to %s mailbox %s: %s",
                "suspend" if suspended else "release", mailbox.email, exc.log_message,
            )
            raise ApprovalError(
                "The Mail Engine could not be updated, so nothing has been changed. "
                "Please try again."
            )

    # Releasing returns the mailbox to ACTIVE rather than to whatever it was
    # before. A mailbox suspended for abuse and then released is one MateMail
    # has decided may send again; restoring a remembered DISABLED would leave
    # the customer unable to use an account we just cleared, for reasons
    # invisible to them.
    mailbox.status = MailboxStatus.SUSPENDED if suspended else MailboxStatus.ACTIVE
    mailbox.save(update_fields=["status", "updated_at"])

    logger.warning(
        "Platform admin %s %s mailbox %s: %s",
        actor.email, "SUSPENDED" if suspended else "RELEASED", mailbox.email,
        reason or "no reason given",
    )
    log_event(
        mailbox.tenant,
        LogEventType.MAILBOX_SUSPENDED if suspended else LogEventType.MAILBOX_UNSUSPENDED,
        source=actor.email,
        mailbox=mailbox,
        metadata={"reason": reason},
    )
    return mailbox
