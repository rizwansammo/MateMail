"""
DNS health checking.

Customer-triggered checks are asynchronous. The previous synchronous endpoint
performed up to four resolver lookups with a 5-second timeout each *inside a
request*, so an unresponsive nameserver could hold a gunicorn worker for ~20
seconds. With four workers, sixteen concurrent checks took the API offline.

The periodic sweep fans out one task per domain rather than looping serially:
a single slow or failing domain used to delay every domain behind it, and at a
few hundred domains the sweep could not finish inside its 15-minute schedule.

Deduplication: a short-lived cache key per domain collapses check storms — a
customer clicking "check" repeatedly, or a sweep overlapping a manual check.
"""
import logging

from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger(__name__)

#: Background sweep budget: at most one automatic check per domain per 5
#: minutes, matching the rate-limit table in docs/SECURITY.md.
BACKGROUND_MIN_INTERVAL_SECONDS = 300

#: How long a queued/running check blocks another for the same domain. Shorter
#: than the background interval so a customer-triggered retry after a genuine
#: failure is not stuck waiting out the full window.
INFLIGHT_LOCK_SECONDS = 60

_INFLIGHT_PREFIX = "dnscheck:inflight:"
_LASTRUN_PREFIX = "dnscheck:lastrun:"


def _inflight_key(domain_id) -> str:
    return f"{_INFLIGHT_PREFIX}{domain_id}"


def _lastrun_key(domain_id) -> str:
    return f"{_LASTRUN_PREFIX}{domain_id}"


def claim_check_slot(domain_id, *, ttl: int = INFLIGHT_LOCK_SECONDS) -> bool:
    """
    Try to claim the right to check this domain.

    `cache.add` is atomic — it sets only if the key is absent — so two
    concurrent callers cannot both claim the same domain. Returns False when a
    check is already queued or running.
    """
    return bool(cache.add(_inflight_key(domain_id), "1", timeout=ttl))


def release_check_slot(domain_id) -> None:
    cache.delete(_inflight_key(domain_id))


def background_check_allowed(domain_id) -> bool:
    """Enforce the 1-per-5-minutes budget for *automatic* checks only."""
    return bool(
        cache.add(_lastrun_key(domain_id), "1", timeout=BACKGROUND_MIN_INTERVAL_SECONDS)
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="dnshealth.check_domain_dns",
)
def check_domain_dns(self, domain_id: str):
    """
    Run the DNS health check for one domain.

    Always releases its in-flight slot, so a crash cannot wedge a domain into
    a permanently un-checkable state.
    """
    from apps.domains.models import Domain

    from .services import check_dns_for_domain

    try:
        domain = Domain.objects.get(pk=domain_id)
    except Domain.DoesNotExist:
        logger.warning("check_domain_dns: domain %s no longer exists", domain_id)
        release_check_slot(domain_id)
        return

    try:
        check_dns_for_domain(domain)
        logger.info("DNS check complete for %s", domain.domain)
    except Exception as exc:
        logger.warning("DNS check failed for %s: %s", domain.domain, exc)
        try:
            raise self.retry(exc=exc)
        finally:
            # A retry re-enqueues, so the slot must be free for it.
            release_check_slot(domain_id)
    else:
        release_check_slot(domain_id)


@shared_task(name="dnshealth.check_all_active_domains_dns")
def check_all_active_domains_dns():
    """
    Periodic sweep. Fans out one task per domain.

    Returns the number of domains enqueued, which makes the beat schedule
    observable in the Celery result backend.

    Enqueuing cannot fail the sweep for other domains: each dispatch is
    guarded, so one bad row does not stop the rest — the failure mode the
    previous serial loop had.
    """
    from apps.domains.models import Domain, DomainStatus

    domain_ids = list(
        Domain.objects.exclude(status=DomainStatus.PAUSED).values_list("id", flat=True)
    )

    enqueued = skipped = failed = 0
    for domain_id in domain_ids:
        try:
            # Respect the background budget, then take the in-flight slot so a
            # sweep cannot pile onto a manual check already running.
            if not background_check_allowed(domain_id):
                skipped += 1
                continue
            if not claim_check_slot(domain_id):
                skipped += 1
                continue
            check_domain_dns.delay(str(domain_id))
            enqueued += 1
        except Exception:
            # One domain must never abort the sweep.
            logger.exception("Could not enqueue DNS check for domain %s", domain_id)
            release_check_slot(domain_id)
            failed += 1

    logger.info(
        "DNS sweep: %d enqueued, %d skipped (rate limit or in flight), %d failed, %d total",
        enqueued, skipped, failed, len(domain_ids),
    )
    return {"enqueued": enqueued, "skipped": skipped, "failed": failed, "total": len(domain_ids)}
