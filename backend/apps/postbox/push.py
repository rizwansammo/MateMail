"""
Native PostBox push: device registrations, engine events and their dispatch.

THE PATH
    Dovecot LMTP saves and commits a delivery -> its push hook reports the
    mailbox, folder, UIDVALIDITY and UID to the Native Engine API -> the API
    relays it to /api/internal/postbox/push-events/ -> `ingest` stores it once
    (by its engine-derived id) and queues `dispatch` -> `dispatch` claims it and
    queues one `send_push` per active device -> `deliver` asks the provider.

    Nothing on this path reads mail. There is no IMAP, no polling and no
    content: an event names a saved message and holds nothing of it.

WHAT MAKES A DEVICE ACTIVE
    Enabled, AND bound to a PostBox session that is neither revoked nor
    expired, AND belonging to a mailbox that may still sign in. So signing out,
    "sign out everywhere", a revoked session, an expired one and a suspended
    mailbox or organization all stop push with no extra bookkeeping.

MAIL DOES NOT WAIT FOR ANY OF THIS
    By the time an event exists the message is already in the mailbox. A
    broker, provider or configuration failure here loses, at worst, a push.

See docs/POSTBOX_REMOTE_PUSH.md for the full contract.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from kombu.exceptions import OperationalError

from .auth import MailboxUnavailable, assert_mailbox_may_sign_in
from .models import PostBoxPushDevice, PostBoxPushEvent
from .push_providers import PushOutcome, provider_for

logger = logging.getLogger(__name__)

#: Registrations kept per mailbox. One per installation and provider, so this
#: is a bound on abuse, not on legitimate use.
MAX_DEVICES_PER_MAILBOX = 20

#: A new-mail notification older than this is not worth sending: the app
#: refreshes on launch, and a push about mail from an hour ago is noise.
EVENT_MAX_AGE = timedelta(minutes=30)
#: How long the sweep leaves an event to the normal path before re-queuing it.
SWEEP_GRACE = timedelta(seconds=60)
SWEEP_BATCH = 200
#: Events are diagnostics once dispatched; nothing needs them for longer.
EVENT_RETENTION = timedelta(days=7)

#: Bumped only with a breaking change to the payload the app parses.
PAYLOAD_VERSION = "1"


class TooManyDevices(Exception):
    pass


# ── registrations ───────────────────────────────────────────────────────────

def register_device(
    *, mailbox, session, installation_id, platform, provider, token_type, token
) -> tuple[PostBoxPushDevice, bool]:
    """
    Create or refresh this installation's registration for this mailbox.

    Keyed by (mailbox, installation, provider), so the app re-registering on
    every launch - with a refreshed FCM token or a new WNS channel - updates
    one row instead of adding rows. The row moves to the CURRENT session: a
    registration always follows the session that last proved it.
    """
    now = timezone.now()
    fields = {
        "session": session,
        "platform": platform,
        "token_type": token_type,
        "token": token,
        "enabled": True,
        "disabled_reason": "",
        "disabled_at": None,
        "last_error": "",
        "last_seen_at": now,
    }
    key = {"mailbox": mailbox, "installation_id": installation_id, "provider": provider}
    with transaction.atomic():
        device = PostBoxPushDevice.objects.select_for_update().filter(**key).first()
        # The same provider identity under another installation id of this
        # mailbox is one device registered twice; keep only the newest.
        PostBoxPushDevice.objects.for_mailbox(mailbox).filter(
            provider=provider, token=token
        ).exclude(installation_id=installation_id).delete()
        if device is None:
            if PostBoxPushDevice.objects.for_mailbox(mailbox).count() >= MAX_DEVICES_PER_MAILBOX:
                raise TooManyDevices
            try:
                with transaction.atomic():
                    return PostBoxPushDevice.objects.create(**key, **fields), True
            except IntegrityError:
                # A concurrent registration of the same installation won.
                device = PostBoxPushDevice.objects.select_for_update().get(**key)
        for name, value in fields.items():
            setattr(device, name, value)
        device.save()
        return device, False


def assert_mailbox_may_receive_push(mailbox) -> None:
    """
    Delivery eligibility is not the same thing as login eligibility.

    A TeamBox is deliberately passwordless and therefore must fail
    assert_mailbox_may_sign_in(), but it is still a real active mailbox whose
    members may receive delivery notifications through their personal sessions.
    """
    from apps.mailboxes.models import MailboxKind, MailboxStatus

    if mailbox.kind != MailboxKind.TEAM_BOX:
        assert_mailbox_may_sign_in(mailbox)
        return

    if mailbox.status != MailboxStatus.ACTIVE:
        raise MailboxUnavailable("This TeamBox is not active.")
    if not mailbox.mail_engine_provisioned:
        raise MailboxUnavailable("This TeamBox is still being set up.")
    if mailbox.tenant is None or not mailbox.tenant.can_use_mail:
        raise MailboxUnavailable("This organization's mail service is not active.")


def active_devices(mailbox):
    """
    Registrations that may receive a push right now, for this mailbox.

    TeamBox registrations are tied to a personal PostBox session. Re-check the
    live TeamBox grant here so revoking Read access also stops future mailbox
    activity notifications without waiting for that device to open PostBox.
    """
    devices = PostBoxPushDevice.objects.for_mailbox(mailbox).filter(
        enabled=True,
        session__revoked_at__isnull=True,
        session__expires_at__gt=timezone.now(),
    )

    from apps.mailboxes.models import MailboxKind

    if mailbox.kind == MailboxKind.TEAM_BOX:
        devices = devices.filter(
            session__mailbox__access_grants__target_mailbox=mailbox,
            session__mailbox__access_grants__grant_type="team_box",
            session__mailbox__access_grants__active=True,
            session__mailbox__access_grants__can_read=True,
        ).distinct()
    return devices


# ── events ──────────────────────────────────────────────────────────────────

def ingest(*, mailbox, event_id, event_type, folder, uid_validity, uid):
    """
    Store the engine's event once. Returns (event, created).

    The id is the engine's, derived from the saved message's identity, so a
    repeated report is the same row and is not queued a second time. The
    dispatch is queued only after the row has committed.
    """
    event, created = PostBoxPushEvent.objects.get_or_create(
        event_id=event_id,
        defaults={
            "mailbox": mailbox,
            "event_type": event_type,
            "folder": folder,
            "uid_validity": uid_validity,
            "uid": uid,
        },
    )
    if created:
        transaction.on_commit(lambda: enqueue(event.event_id))
    return event, created


def enqueue(event_id) -> bool:
    """
    Queue the dispatch. False when the broker cannot be reached.

    The event is already stored as PENDING, so a failure here is not a loss:
    the beat sweep re-queues it within a minute or two.
    """
    from .tasks import dispatch_push_event

    try:
        dispatch_push_event.delay(str(event_id))
    except (OperationalError, OSError) as exc:
        logger.warning(
            "PostBox push: event %s not queued (%s); the sweep will retry",
            event_id, type(exc).__name__,
        )
        return False
    return True


def dispatch(event_id) -> str:
    """Claim an event and queue one send per active device."""
    from .tasks import send_push

    event = (
        PostBoxPushEvent.objects.select_related("mailbox", "mailbox__tenant")
        .filter(pk=event_id)
        .first()
    )
    if event is None:
        return "missing"
    if timezone.now() - event.created_at > EVENT_MAX_AGE:
        PostBoxPushEvent.objects.filter(
            pk=event.pk, state=PostBoxPushEvent.State.PENDING
        ).update(state=PostBoxPushEvent.State.EXPIRED)
        return "expired"
    # The conditional UPDATE is what makes a re-queue safe: the sweep and the
    # normal path may both deliver this task, and only one of them fans out.
    if not event.claim():
        return "claimed"
    try:
        assert_mailbox_may_receive_push(event.mailbox)
    except MailboxUnavailable:
        return "mailbox_unavailable"
    devices = list(active_devices(event.mailbox).values_list("id", flat=True))
    PostBoxPushEvent.objects.filter(pk=event.pk).update(devices=len(devices))
    for device_id in devices:
        send_push.delay(str(event.event_id), str(device_id))
    logger.info("PostBox push: event %s dispatched to %d device(s)", event.event_id, len(devices))
    return "dispatched"


def push_message(event, device) -> dict[str, str]:
    """
    Everything a push carries. Opaque identifiers, as strings (FCM's `data`
    must be a string map; WNS gets the same map as JSON).

    Deliberately absent: the mailbox address - the app knows which of its
    accounts `device_registration_id` belongs to - and anything about the
    message beyond where it is.
    """
    message = {
        "version": PAYLOAD_VERSION,
        "kind": event.event_type,
        "event_id": str(event.event_id),
        "device_registration_id": str(device.id),
        "folder": event.folder,
    }
    if event.uid_validity is not None and event.uid is not None:
        message["uid_validity"] = str(event.uid_validity)
        message["uid"] = str(event.uid)
    return message


def deliver(event_id, device_id) -> PushOutcome:
    """Send one event to one device, and record what the provider said."""
    device = (
        PostBoxPushDevice.objects.select_related("session", "mailbox", "mailbox__tenant")
        .filter(pk=device_id)
        .first()
    )
    event = PostBoxPushEvent.objects.filter(pk=event_id).first()
    if device is None or event is None or device.mailbox_id != event.mailbox_id:
        return PushOutcome(PushOutcome.REJECTED, "gone")
    # Re-checked at send time, because a retry can run minutes after the
    # dispatch: a session revoked, a device disabled or deleted, or a mailbox
    # or organization suspended since then receives nothing.
    if not device.enabled or not device.session.is_active:
        return PushOutcome(PushOutcome.REJECTED, "inactive")
    try:
        assert_mailbox_may_receive_push(device.mailbox)
    except MailboxUnavailable:
        return PushOutcome(PushOutcome.REJECTED, "mailbox_unavailable")

    outcome = provider_for(device.provider).send(device, push_message(event, device))
    _record(device, outcome)
    return outcome


def _record(device, outcome: PushOutcome) -> None:
    now = timezone.now()
    rows = PostBoxPushDevice.objects.filter(pk=device.pk)
    if outcome.status == PushOutcome.DELIVERED:
        rows.update(last_push_at=now, last_error="")
    elif outcome.status == PushOutcome.INVALID_DEVICE:
        # The provider says it will never work again. Retrying it would be a
        # request per new message for ever; the app registers afresh on launch.
        rows.update(
            enabled=False, disabled_reason=outcome.code, disabled_at=now, last_error=outcome.code
        )
    else:
        rows.update(last_error=outcome.code)
    # The device id and a short code. Never the token, the channel URI or the
    # mailbox address.
    logger.info(
        "PostBox push to device %s via %s: %s (%s)",
        device.id, device.provider, outcome.status, outcome.code,
    )


def retry_delay(retries: int, retry_after: int | None) -> int:
    """30 s, 60 s, 120 s - or the provider's Retry-After - capped at 15 min."""
    return min(max(retry_after or 0, 30 * 2 ** retries), 900)


# ── housekeeping ────────────────────────────────────────────────────────────

def sweep() -> dict:
    """
    Re-queue events Celery never received, and expire stale ones.

    SERVER EVENT RETRY, NOT MAILBOX POLLING: it reads the event table only.
    """
    now = timezone.now()
    expired = PostBoxPushEvent.objects.filter(
        state=PostBoxPushEvent.State.PENDING, created_at__lt=now - EVENT_MAX_AGE
    ).update(state=PostBoxPushEvent.State.EXPIRED)
    waiting = PostBoxPushEvent.objects.filter(
        state=PostBoxPushEvent.State.PENDING, created_at__lt=now - SWEEP_GRACE
    ).values_list("event_id", flat=True)[:SWEEP_BATCH]
    requeued = sum(1 for event_id in waiting if enqueue(event_id))
    if expired or requeued:
        logger.info("PostBox push sweep: requeued=%d expired=%d", requeued, expired)
    return {"requeued": requeued, "expired": expired}


def prune() -> int:
    """Delete events past their short retention. They hold no content."""
    deleted, _ = PostBoxPushEvent.objects.filter(
        created_at__lt=timezone.now() - EVENT_RETENTION
    ).delete()
    if deleted:
        logger.info("PostBox push: pruned %d event(s)", deleted)
    return deleted
