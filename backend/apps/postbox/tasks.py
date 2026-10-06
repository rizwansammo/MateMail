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
    ).select_related("mailbox", "mailbox__tenant", "submission_mailbox")[:200]

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
        ScheduledMessage.objects.select_related(
            "mailbox",
            "mailbox__tenant",
            "submission_mailbox",
            "submission_mailbox__tenant",
        )
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
    submission_mailbox = row.submission_mailbox or mailbox

    try:
        # Re-checked at send time, not only when it was scheduled. An
        # organization suspended between scheduling and sending must not send.
        sending.assert_organization_may_send(mailbox)

        if submission_mailbox.pk != mailbox.pk:
            from .auth import assert_mailbox_may_sign_in

            assert_mailbox_may_sign_in(submission_mailbox)

        with imap.open_mailbox(mailbox.email) as connection:
            info = connection.select(row.folder, readonly=True)
            if info.uid_validity and row.uid_validity and info.uid_validity != row.uid_validity:
                raise sending.SendFailed(
                    "The scheduled message could not be found.",
                    f"UIDVALIDITY changed for {row.folder}: "
                    f"stored={row.uid_validity} actual={info.uid_validity}",
                )
            raw = connection.fetch_raw(row.uid)

        message, from_address, recipients = _scheduled_message_for_delivery(
            mailbox,
            raw,
            actor_mailbox=submission_mailbox,
        )
        sending.submit(
            message,
            mailbox=submission_mailbox,
            envelope_from=from_address,
            recipients=recipients,
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
            connection.append(
                roles.get("sent", "Sent"),
                message.as_bytes(),
                flags="\\Seen",
            )
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

    if submission_mailbox.pk != mailbox.pk:
        from apps.logs.utils import log_event

        log_event(
            mailbox.tenant,
            "postbox_scheduled_mailbox_sent_by_actor",
            source=submission_mailbox.email,
            metadata={
                "target_mailbox": mailbox.email,
                "scheduled_message_id": str(row.id),
                "message_id": message.get("Message-ID", "") or "",
            },
        )

    logger.info("PostBox scheduled %s sent for mailbox %s", scheduled_id, mailbox.pk)
    return "sent"



def _scheduled_message_for_delivery(mailbox, raw: bytes, *, actor_mailbox=None):
    """
    Finalise one Scheduled source message.

    New scheduled messages are stored in the same editable format as Drafts:
    Bcc and signature choice are metadata, and the signature is applied only
    here. Legacy rows created before this format existed are sent exactly as
    stored so an already-applied HTML/image signature is never duplicated or
    reconstructed incorrectly.
    """
    import email as email_module
    import email.policy
    import email.utils

    from . import mime, sending, signatures

    parsed = mime.parse_message(raw, load_remote_images=True)

    if not parsed.draft_state:
        message = email_module.message_from_bytes(raw, policy=email.policy.default)
        recipients = sending.envelope_recipients(
            _addresses(message, "To"),
            _addresses(message, "Cc"),
            [],
        )
        if not recipients:
            raise sending.SendFailed(
                "The scheduled message had no recipients.",
                "legacy scheduled source has no envelope recipients",
            )
        from_address = email_module.utils.parseaddr(message.get("From", ""))[1]
        identity = sending.assert_may_send_as(
            mailbox,
            from_address,
            actor_mailbox=actor_mailbox or mailbox,
        )
        if identity.send_mode == "on_behalf" and (actor_mailbox or mailbox).pk != mailbox.pk:
            actor = actor_mailbox or mailbox
            if "Sender" in message:
                del message["Sender"]
            message["Sender"] = email_module.utils.formataddr((
                actor.full_name or "",
                actor.email,
            ))
        return message, from_address, recipients

    actor = actor_mailbox or mailbox
    identity = sending.assert_may_send_as(
        mailbox,
        parsed.from_address,
        actor_mailbox=actor,
    )
    signature = signatures.for_mailbox(mailbox, parsed.draft_signature_id or None)
    if parsed.draft_signature_id and signature is None:
        raise sending.SendFailed(
            "The selected signature is no longer available.",
            (
                "scheduled source references missing signature "
                f"{parsed.draft_signature_id}"
            ),
        )

    # Editable scheduled replies store their opted-in quote as ordinary MIME
    # text with verified draft markers. Apply signature BEFORE the quote.
    draft_body, quoted_text = mime.split_draft_reply_quote(parsed)
    text, html, related = signatures.apply(
        text=draft_body,
        html="" if quoted_text else parsed.html,
        signature=signature,
    )
    text, html = mime.append_reply_quote(text, html, quoted_text)

    attachments = []
    for attachment in parsed.attachments:
        try:
            attachments.append(mime.extract_attachment(raw, attachment.part_id))
        except KeyError as exc:
            raise sending.SendFailed(
                "A scheduled attachment could not be found.",
                f"missing scheduled attachment part {attachment.part_id}",
            ) from exc

    recipients = sending.envelope_recipients(parsed.to, parsed.cc, parsed.bcc)
    if not recipients:
        raise sending.SendFailed(
            "The scheduled message had no recipients.",
            "editable scheduled source has no envelope recipients",
        )

    message = mime.build_message(
        from_address=identity.address,
        from_name=identity.name,
        to=parsed.to,
        cc=parsed.cc,
        bcc=parsed.bcc,
        subject=parsed.subject,
        text=text,
        html=html,
        in_reply_to=parsed.in_reply_to,
        references=parsed.references,
        attachments=attachments,
        related=related,
        message_id=parsed.message_id,
    )
    if identity.send_mode == "on_behalf" and actor.pk != mailbox.pk:
        message["Sender"] = email_module.utils.formataddr((
            actor.full_name or "",
            actor.email,
        ))
    return message, identity.address, recipients


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


# ── native push (see apps.postbox.push) ─────────────────────────────────────
#
# A push is safe to retry where a send is not: the SAME event_id travels in
# every attempt and the app drops a repeat. It is still retried only for
# transient failures, a bounded number of times, and never for a device the
# provider has declared invalid.

@shared_task(name="postbox.dispatch_push_event")
def dispatch_push_event(event_id: str) -> str:
    """Claim one engine event and fan it out to the mailbox's active devices."""
    from . import push

    return push.dispatch(event_id)


@shared_task(name="postbox.send_push", bind=True, max_retries=3)
def send_push(self, event_id: str, device_id: str) -> str:
    """Send one event to one device. Retries only a RETRY outcome."""
    from . import push
    from .push_providers import PushOutcome

    outcome = push.deliver(event_id, device_id)
    if outcome.status == PushOutcome.RETRY and self.request.retries < self.max_retries:
        raise self.retry(countdown=push.retry_delay(self.request.retries, outcome.retry_after))
    return outcome.code


@shared_task(name="postbox.sweep_push_events")
def sweep_push_events() -> dict:
    """Re-queue events Celery never received; expire ones too old to announce."""
    from . import push

    return push.sweep()


@shared_task(name="postbox.prune_push_events")
def prune_push_events() -> int:
    """Delete push events past their week of retention."""
    from . import push

    return push.prune()
