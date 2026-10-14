"""Off by default; tenant data never touches shared logs or a public collector."""
import logging
from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from .ingest import ingest_mailbox_once, configured_receiver_addresses
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
        addresses = configured_receiver_addresses()
        if len(addresses) == 1:
            return ingest_mailbox_once()
        totals: dict[str, int] = {}
        # A single existing Redis lock covers BOTH independent UID cursors.
        # Retry after failure resumes each cursor separately; content hashes
        # suppress duplicate reports arriving through both addresses.
        for address in addresses:
            result = ingest_mailbox_once(address=address)
            for key, count in result.items():
                totals[key] = totals.get(key, 0) + count
        return totals
    except Exception:
        logger.error("TLS-RPT ingestion failed; UID cursor preserved for retry")
        raise
    finally:
        cache.delete(lock)


@shared_task(name="tls_reports.prune")
def prune():
    return {"deleted_records": prune_old_reports()}
