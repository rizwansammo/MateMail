"""TLS-RPT fixed read-only IMAP reader, separate from DMARC and customer mail."""
import logging
from email import policy
from email.parser import BytesParser

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.postbox.imap import open_mailbox
from .authentication import require_authenticated_report
from .headers import validate_report_headers
from .models import TlsIngestCursor
from .parser import InvalidTlsReport, MAX_ARCHIVE, MAX_PARTS, parse_json, unpack
from .services import store_policy

logger = logging.getLogger(__name__)
ADDRESS = "tlsrpt@mail.matemail.pro"
MAX_MESSAGE_BYTES = 6 * 1024 * 1024
MAX_MESSAGES_PER_RUN = 40


def ingest_raw_message(raw):
    if len(raw) > MAX_MESSAGE_BYTES:
        raise InvalidTlsReport("MIME exceeds size cap")
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except (ValueError, TypeError) as exc:
        raise InvalidTlsReport("Invalid MIME") from exc
    # RFC8460 requires a valid reporting-domain DKIM signature. Untrusted
    # sender headers or Rspamd Authentication-Results alone are not proof.
    require_authenticated_report(raw, message)
    attachments = list(message.iter_attachments())
    if not 1 <= len(attachments) <= MAX_PARTS:
        raise InvalidTlsReport("No supported attachments")
    # Parse and validate ALL attachments before storing any tenant telemetry.
    # A malformed second attachment must not partially persist the first.
    verified_policies = []
    for part in attachments:
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or len(payload) > MAX_ARCHIVE:
            raise InvalidTlsReport("Attachment exceeds cap")
        document = unpack(payload, part.get_filename() or "", part.get_content_type())
        policies = parse_json(document)
        validate_report_headers(message, document, policies)
        verified_policies.extend(policies)
    totals = {"stored": 0, "duplicate": 0, "unmanaged": 0}
    with transaction.atomic():
        for parsed in verified_policies:
            totals[store_policy(parsed).status] += 1
    return totals


def ingest_mailbox_once():
    if not getattr(settings, "TLS_RPT_INGEST_ENABLED", False):
        return {"disabled": 1}
    address = getattr(settings, "TLS_RPT_REPORT_ADDRESS", "").lower().strip()
    if address != ADDRESS:
        raise ValueError("Wrong TLS-RPT receiver mailbox")
    cursor, _ = TlsIngestCursor.objects.get_or_create(address=address)
    totals = {"stored": 0, "duplicate": 0, "unmanaged": 0, "rejected": 0, "processed": 0}
    with open_mailbox(address) as mailbox:
        selected = mailbox.select("INBOX", readonly=True)
        if selected.uid_validity <= 0:
            raise RuntimeError("Missing IMAP UIDVALIDITY")
        if cursor.uid_validity != selected.uid_validity:
            cursor.uid_validity, cursor.last_uid = selected.uid_validity, 0
            cursor.save(update_fields=["uid_validity", "last_uid"])
        uids = mailbox.search_uids(["UID", str(cursor.last_uid + 1) + ":*"], newest=False)
        for uid in uids[:MAX_MESSAGES_PER_RUN]:
            if uid <= cursor.last_uid:
                continue
            rejected = False
            try:
                summaries = mailbox.fetch_summaries([uid])
                if not summaries or summaries[0].size > MAX_MESSAGE_BYTES:
                    raise InvalidTlsReport("Message size invalid")
                results = ingest_raw_message(mailbox.fetch_raw(uid))
                for key, value in results.items():
                    totals[key] += value
            except InvalidTlsReport:
                rejected = True
                totals["rejected"] += 1
                logger.warning("Skipped unsafe TLS-RPT report at IMAP UID %s", uid)
            # Retry transient IMAP / database errors; never advance after exceptions.
            cursor.last_uid = uid
            cursor.last_success_at = timezone.now()
            cursor.processed_count += 1
            cursor.rejected_count += int(rejected)
            cursor.save(update_fields=["last_uid", "last_success_at", "processed_count", "rejected_count"])
            totals["processed"] += 1
    cursor.last_checked_at = timezone.now()
    cursor.save(update_fields=["last_checked_at"])
    return totals
