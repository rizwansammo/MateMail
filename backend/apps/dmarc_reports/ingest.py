"""Read-only ingestion from the already-provisioned DMARC mailbox.

No third-party SMTP service, mailbox password duplication, message deletion,
IMAP flag mutation, or report/raw MIME persistence. Disabled until DNS rollout.
"""
import logging
from email import policy
from email.parser import BytesParser

from django.conf import settings
from django.utils import timezone

from apps.postbox.imap import open_mailbox

from .models import IngestCursor
from .parser import (
    InvalidReport, MAX_ARCHIVE_BYTES, MAX_DOCUMENTS,
    extract_xml_documents, parse_xml,
)
from .services import store_report

logger = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 6 * 1024 * 1024
MAX_MESSAGES_PER_RUN = 60


def ingest_raw_message(raw: bytes) -> dict[str, int]:
    """Accept only small RFC822 attachments, never message text or HTML."""
    if len(raw) > MAX_MESSAGE_BYTES:
        raise InvalidReport("Message exceeds ingestion cap")
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except (ValueError, TypeError) as exc:
        raise InvalidReport("Invalid MIME message") from exc
    attachments = list(message.iter_attachments())
    if not attachments or len(attachments) > MAX_DOCUMENTS:
        raise InvalidReport("No supported DMARC attachments")
    counters = {"stored": 0, "duplicate": 0, "unmanaged": 0}
    num_documents = 0
    for part in attachments:
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or len(payload) > MAX_ARCHIVE_BYTES:
            raise InvalidReport("Attachment exceeded size cap")
        name = part.get_filename() or ""
        documents = extract_xml_documents(payload, name, part.get_content_type())
        num_documents += len(documents)
        if num_documents > MAX_DOCUMENTS:
            raise InvalidReport("Too many DMARC documents")
        for document in documents:
            parsed = parse_xml(document)
            result = store_report(parsed)
            counters[result.status] += 1
    return counters


def ingest_mailbox_once() -> dict[str, int]:
    """Bounded, idempotent IMAP polling; never mark messages read or delete.

    The Celery task takes a cross-worker Redis cache lock. UIDVALIDITY
    changes reset the cursor, but report XML hashes suppress duplicate DB
    writes. Cursor advances only on stored, duplicate, unmanaged or explicitly
    rejected message, and persists after each UID.
    """
    if not getattr(settings, "DMARC_REPORT_INGEST_ENABLED", False):
        return {"disabled": 1}
    address = getattr(settings, "DMARC_REPORT_ADDRESS", "").lower().strip()
    if not address or address != "dmarc@mail.matemail.pro":
        raise ValueError("P4-B is restricted to its dedicated verified intake mailbox")

    cursor, _ = IngestCursor.objects.get_or_create(address=address)
    counters = {"stored": 0, "duplicate": 0, "unmanaged": 0, "rejected": 0, "processed": 0}
    with open_mailbox(address) as mailbox:
        selected = mailbox.select("INBOX", readonly=True)
        validity = selected.uid_validity
        if validity <= 0:
            raise RuntimeError("IMAP UIDVALIDITY not available; refusing unsafe cursor")
        if cursor.uid_validity != validity:
            cursor.uid_validity = validity
            cursor.last_uid = 0
            cursor.save(update_fields=["uid_validity", "last_uid"])

        # Bounded UID range instead of an unbounded SEARCH ALL. Process
        # oldest first so a busy reporting mailbox cannot starve old reports.
        wanted = mailbox.search_uids(["UID", f"{cursor.last_uid + 1}:*"], newest=False)
        for uid in wanted[:MAX_MESSAGES_PER_RUN]:
            if uid <= cursor.last_uid:
                continue
            try:
                summary = mailbox.fetch_summaries([uid])
                if not summary or summary[0].size > MAX_MESSAGE_BYTES:
                    raise InvalidReport("Missing or oversized RFC822 message")
                raw = mailbox.fetch_raw(uid)
                result = ingest_raw_message(raw)
                for key, value in result.items():
                    counters[key] += value
            except InvalidReport:
                counters["rejected"] += 1
                # Reject this poison message once; keep the actual email in
                # INBOX untouched for privileged operator examination.
                logger.warning("Skipped malformed DMARC report at IMAP UID %s", uid)
            # Exceptions caused by DB/network failure bubble up: the cursor
            # is not advanced, so a retry can safely process this UID again.
            cursor.last_uid = uid
            cursor.last_success_at = timezone.now()
            cursor.save(update_fields=["last_uid", "last_success_at"])
            counters["processed"] += 1

    cursor.last_checked_at = timezone.now()
    cursor.last_error = ""
    cursor.save(update_fields=["last_checked_at", "last_error"])
    return counters
