"""Controlled DMARC background jobs. Ingestion is OFF unless explicitly enabled."""
import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from .ingest import ingest_mailbox_once
from .services import prune_old_reports

logger = logging.getLogger(__name__)


@shared_task(name="dmarc_reports.poll_mailbox")
def poll_mailbox():
    if not getattr(settings, "DMARC_REPORT_INGEST_ENABLED", False):
        return {"disabled": 1}
    # Atomic, time-bound cross-worker claim. Never run parallel reads of the
    # same cursor (two workers could skip each other's UID ranges).
    key = "dmarc:mailbox-ingest-lock"
    if not cache.add(key, "1", timeout=540):
        return {"already_running": 1}
    try:
        return ingest_mailbox_once()
    except Exception:
        # Avoid logging raw RFC822, XML, mail headers, credentials or payloads.
        logger.error("DMARC mailbox ingestion failed; cursor remains retryable")
        raise
    finally:
        cache.delete(key)


@shared_task(name="dmarc_reports.prune")
def prune():
    return {"deleted_records": prune_old_reports()}
