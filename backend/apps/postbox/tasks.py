"""
Scheduled send, executed by Celery.

THE DOUBLE-SEND PROBLEM
    Celery retries. A worker can die after Postfix accepted a message and
    before the row was updated, and the naive implementation then sends the
    same message again on the next beat — to the customer, twice, with the
    same Message-ID.

    `ScheduledMessage.claim()` is the answer: a conditional UPDATE from
    PENDING to SENDING that reports whether it won. Two workers racing produce
    one winner. A retry after a crash finds the row in SENDING and declines
    rather than sending again.

    The deliberate consequence is that a crash mid-send leaves the row stuck
    in SENDING rather than being retried automatically. That is the correct
    trade: a message stuck in SENDING is visible, recoverable and has been
    sent at most once, whereas an automatic retry may send it twice. A human
    decides, with the `sent_message_id` column to tell them what happened.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="postbox.dispatch_scheduled_messages")
def dispatch_scheduled_messages() -> dict:
    """
    Send everything that is due. Run on a beat schedule.

    Each message is claimed and sent independently — one failure must not stop
    the rest of the queue, which is why the loop catches per row.
    """
    from .models import ScheduledMessage

    due = ScheduledMessage.objects.filter(
        state=ScheduledMessage.State.PENDING,
        scheduled_at__lte=timezone.now(),
    ).select_related("mailbox", "mailbox__tenant")[:200]

    sent = failed = skipped = 0
    for row in due:
        outcome = send_scheduled_message(str(row.id))
        if outcome == "sent":
            sent += 1
        elif outcome == "failed":
            failed += 1
        else:
            skipped += 1

    if sent or failed:
        logger.info(
            "PostBox scheduled dispatch: sent=%d failed=%d skipped=%d", sent, failed, skipped
        )
    return {"sent": sent, "failed": failed, "skipped": skipped}


@shared_task(name="postbox.send_scheduled_message")
def send_scheduled_message(scheduled_id: str) -> str:
    """
    Send one scheduled message. Returns "sent", "failed" or "skipped".

    Deliberately NOT `autoretry_for`. Retrying a send is exactly the behaviour
    that causes duplicates; the claim below is what makes this safe, and a
    failure is recorded for a human rather than replayed by a machine.
    """
    from . import imap, sending
    from .models import ScheduledMessage

    row = (
        ScheduledMessage.objects.select_related("mailbox", "mailbox__tenant")
        .filter(pk=scheduled_id)
        .first()
    )
    if row is None:
        logger.warning("PostBox: scheduled message %s no longer exists", scheduled_id)
        return "skipped"

    if not row.claim():
        # Another worker has it, or it already ran. Both mean: do nothing.
        logger.info("PostBox: scheduled %s was already claimed", scheduled_id)
        return "skipped"

    mailbox = row.mailbox

    try:
        # Re-checked at send time, not only when it was scheduled. An
        # organization suspended between scheduling and sending must not send.
        sending.assert_organization_may_send(mailbox)

        with imap.open_mailbox(mailbox.email) as connection:
            info = connection.select(row.folder, readonly=True)
            if info.uid_validity and row.uid_validity and info.uid_validity != row.uid_validity:
                raise sending.SendFailed(
                    "The scheduled message could not be found.",
                    f"UIDVALIDITY changed for {row.folder}: "
                    f"stored={row.uid_validity} actual={info.uid_validity}",
                )
            raw = connection.fetch_raw(row.uid)

        import email as email_module
        import email.policy

        message = email_module.message_from_bytes(raw, policy=email.policy.default)

        recipients = sending.envelope_recipients(
            _addresses(message, "To"), _addresses(message, "Cc"), []
        )
        if not recipients:
            raise sending.SendFailed(
                "The scheduled message had no recipients.", "no envelope recipients"
            )

        from_address = email_module.utils.parseaddr(message.get("From", ""))[1]
        sending.assert_may_send_as(mailbox, from_address)
        sending.submit(
            message, mailbox=mailbox, envelope_from=from_address, recipients=recipients
        )

    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
        message_text = getattr(exc, "customer_message", None) or "The message could not be sent."
        log_text = getattr(exc, "log_message", None) or repr(exc)
        ScheduledMessage.objects.filter(pk=row.pk).update(
            state=ScheduledMessage.State.FAILED,
            last_error=message_text,
            updated_at=timezone.now(),
        )
        logger.error("PostBox scheduled %s failed: %s", scheduled_id, log_text)
        return "failed"

    # Sent. File it, remove it from Scheduled, and record what went.
    filed = False
    try:
        with imap.open_mailbox(mailbox.email) as connection:
            roles = {f.role: f.name for f in connection.list_folders() if f.role}
            connection.append(roles.get("sent", "Sent"), raw, flags="\\Seen")
            filed = True
            connection.select(row.folder)
            connection.delete_permanently([row.uid])
    except Exception as exc:  # noqa: BLE001
        # The mail has gone. Filing problems are logged and do not change the
        # outcome — marking this FAILED would invite somebody to send it twice.
        logger.error(
            "PostBox scheduled %s was sent but not filed (filed=%s): %r",
            scheduled_id, filed, exc,
        )

    ScheduledMessage.objects.filter(pk=row.pk).update(
        state=ScheduledMessage.State.SENT,
        sent_at=timezone.now(),
        sent_message_id=message.get("Message-ID", "") or "",
        last_error="",
        updated_at=timezone.now(),
    )
    logger.info("PostBox scheduled %s sent for mailbox %s", scheduled_id, mailbox.pk)
    return "sent"


def _addresses(message, header: str) -> list[str]:
    import email.utils

    return [
        address
        for _, address in email.utils.getaddresses([message.get(header, "") or ""])
        if address
    ]


@shared_task(name="postbox.prune_expired_sessions")
def prune_expired_sessions() -> int:
    """
    Delete sessions that expired a while ago.

    Kept for a week past expiry so Settings → Security can still show a person
    the device that was signed in when something went wrong; after that the row
    is only a record of an IP address, which is not worth retaining.
    """
    from datetime import timedelta

    from .models import PostBoxSession

    cutoff = timezone.now() - timedelta(days=7)
    deleted, _ = PostBoxSession.objects.filter(expires_at__lt=cutoff).delete()
    if deleted:
        logger.info("PostBox: pruned %d expired session(s)", deleted)
    return deleted
