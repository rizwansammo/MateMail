"""
PostBox new-mail events: Dovecot -> this API -> MateMail.

WHAT THIS DOES
    Dovecot's LMTP hook (deploy/native-engine/dovecot/postbox-push.lua) reports
    every committed delivery here: the mailbox address, the folder, UIDVALIDITY
    and UID. This module validates the report, gives it a stable event id and
    relays it to MateMail's private ingest across `matemail_engine_link`, where
    MateMail stores it once and sends the remote push to that mailbox's PostBox
    devices.

WHY IT IS SHAPED THIS WAY
    DOVECOT NEVER WAITS FOR MATEMAIL. Dovecot is answered as soon as a report is
    validated and queued; the relay runs on its own thread. A slow or absent
    MateMail costs a delivery nothing, and a full queue drops an event (logged)
    rather than holding one up. Push is supplementary; mail is not.

    THE EVENT ID IS DERIVED, NOT RANDOM. The same saved message - same mailbox,
    folder, UIDVALIDITY and UID - always gets the same id (UUIDv5), so a repeated
    report or a relay retry is the same event to MateMail, which stores each id
    once. No message content goes into it; there is none to put in.

    NO CONTENT IS ACCEPTED. Exactly four fields are allowed. A report carrying
    anything else - a subject, a sender, a snippet - is refused outright rather
    than trimmed, so a hook that began leaking content would fail loudly.

    THE RELAY TARGET IS CONFIGURATION. The URL comes from the environment, never
    from a request, redirects are not followed and no proxy is used, so no
    caller can make the API send its MateMail credential anywhere else.

WHICH PHASE OWNS IT
    PostBox remote push, server half. See docs/POSTBOX_REMOTE_PUSH.md.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid

import validation
from validation import ValidationError

logger = logging.getLogger("native_api.push")

#: Dovecot's push credential. SEPARATE from the policy and provisioning
#: secrets on purpose: it opens `/v1/dovecot/push` and nothing else, so a
#: compromised Dovecot can report fake deliveries at worst - never provision.
DOVECOT_PUSH_SECRET = os.environ.get("NATIVE_DOVECOT_PUSH_SECRET", "")

#: MateMail's private ingest, and the credential MateMail expects there
#: (its POSTBOX_PUSH_INGEST_SECRET). Either one empty disables the relay.
POSTBOX_PUSH_URL = os.environ.get("NATIVE_POSTBOX_PUSH_URL", "")
POSTBOX_PUSH_SECRET = os.environ.get("NATIVE_POSTBOX_PUSH_SECRET", "")

#: NEVER CHANGE IT. Every event id derives from this; a new namespace would
#: make every past delivery look like a new event to MateMail.
EVENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://matemail.online/ns/postbox-new-mail")

#: IMAP UIDs and UIDVALIDITY are non-zero unsigned 32-bit numbers (RFC 9051).
_UINT32_MAX = 4_294_967_295
MAX_FOLDER_LENGTH = 255
FIELDS = frozenset({"mailbox", "folder", "uid_validity", "uid"})

QUEUE_SIZE = 1000
RELAY_TIMEOUT_SECONDS = 3
#: Pauses before the second and third attempt. Retried only when MateMail did
#: not answer (connection failure, timeout, 429, 5xx) - never for a refusal.
RETRY_DELAYS = (2, 5)


def dovecot_authorized(provided: str) -> bool:
    """Constant-time check of Dovecot's push credential. Unset denies."""
    if not DOVECOT_PUSH_SECRET:
        return False
    return hmac.compare_digest(provided.encode(), DOVECOT_PUSH_SECRET.encode())


def _identifier(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _UINT32_MAX:
        raise ValidationError(f"{field} must be an integer from 1 to {_UINT32_MAX}", field)
    return value


def _folder(value) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_FOLDER_LENGTH
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValidationError("folder must be a folder name", "folder")
    return value


def event_id(mailbox: str, folder: str, uid_validity: int, uid: int) -> str:
    """The stable id of one saved message. Identity only, never content."""
    return str(uuid.uuid5(EVENT_NAMESPACE, f"new_mail\n{mailbox}\n{folder}\n{uid_validity}\n{uid}"))


def new_mail_event(body) -> dict:
    """Dovecot's report, validated, as the event MateMail ingests."""
    validation.payload(body, allowed=set(FIELDS), required=set(FIELDS))
    mailbox = validation.email_address(body["mailbox"], field="mailbox")
    folder = _folder(body["folder"])
    uid_validity = _identifier(body["uid_validity"], "uid_validity")
    uid = _identifier(body["uid"], "uid")
    return {
        "event_id": event_id(mailbox, folder, uid_validity, uid),
        "event": "new_mail",
        "mailbox": mailbox,
        "folder": folder,
        "uid_validity": uid_validity,
        "uid": uid,
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, not an instruction: the secret stays put."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


class Relay:
    """
    Forwards events to MateMail from one background thread.

    `submit` never blocks and never raises: it runs while Dovecot waits. The
    queue is bounded, so an outage fills it and then drops events - it never
    grows without limit and never slows the mail path.
    """

    def __init__(
        self,
        url: str = POSTBOX_PUSH_URL,
        secret: str = POSTBOX_PUSH_SECRET,
        *,
        send=None,
        sleep=time.sleep,
        maxsize: int = QUEUE_SIZE,
        autostart: bool = True,
    ):
        self.url = url
        self.secret = secret
        self._send = send or self._post
        self._sleep = sleep
        #: False only in tests, which drive the queue by hand.
        self._autostart = autostart
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.secret)

    def start(self) -> None:
        with self._lock:
            if self._thread is None and self.enabled:
                self._thread = threading.Thread(
                    target=self._run, name="postbox-push-relay", daemon=True
                )
                self._thread.start()

    def submit(self, event: dict) -> bool:
        """Queue one event. False when the relay is off or the queue is full."""
        if not self.enabled:
            return False
        if self._autostart:
            self.start()
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            logger.warning("PostBox push relay queue is full; event %s dropped", event["event_id"])
            return False
        return True

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            try:
                self.deliver(event)
            except Exception as exc:  # noqa: BLE001 - one event must not stop the relay
                logger.error(
                    "PostBox push relay: %s for event %s", type(exc).__name__, event.get("event_id")
                )
            finally:
                self._queue.task_done()

    def deliver(self, event: dict) -> str:
        """Send one event, retrying only an unanswered one. Returns the outcome."""
        for attempt in range(len(RETRY_DELAYS) + 1):
            if attempt:
                self._sleep(RETRY_DELAYS[attempt - 1])
            status = self._send(event)
            if 200 <= status < 300:
                return "delivered"
            if status and status not in (408, 429) and status < 500:
                logger.warning(
                    "PostBox push relay: MateMail answered %s for event %s", status, event["event_id"]
                )
                return "refused"
        logger.warning("PostBox push relay: MateMail did not accept event %s", event["event_id"])
        return "unreachable"

    def _post(self, event: dict) -> int:
        """POST one event. The HTTP status, or 0 when MateMail could not be asked."""
        request = urllib.request.Request(
            self.url,
            data=json.dumps(event).encode(),
            headers={"Content-Type": "application/json", "X-PostBox-Push-Secret": self.secret},
            method="POST",
        )
        try:
            with _OPENER.open(request, timeout=RELAY_TIMEOUT_SECONDS) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except (urllib.error.URLError, OSError, ValueError):
            return 0


#: The API's one relay. Started on first use, so importing this module - as the
#: tests do - never starts a thread.
relay = Relay()
