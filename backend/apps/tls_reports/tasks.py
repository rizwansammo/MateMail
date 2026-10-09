"""Off by default; tenant data never touches shared logs or a public collector."""
import logging
from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from .ingest import ingest_mailbox_once
from .services import prune_old_reports

logger = logging.getLogger(__name__)


@shared_task(name="tls_reports.poll_mailbox")
def poll_mailbox():
    if not getattr(settings, "TLS_RPT_INGEST_ENABLED", False):
        return {"disabled": 1}
    lock = "tls-rpt:mailbox-poll-lock"
    if not cache.add(lock, "1", timeout=540):
        return {"already_running": 1}
    try:
        return ingest_mailbox_once()
    except Exception:
        logger.error("TLS-RPT ingestion failed; UID cursor preserved for retry")
        raise
    finally:
        cache.delete(lock)


@shared_task(name="tls_reports.prune")
def prune():
    return {"deleted_records": prune_old_reports()}
