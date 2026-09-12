"""
Periodic domain ownership re-verification.

## Why ownership has to be re-checked

Before P5, ownership was proved once and never looked at again. That is fine
right up until a domain changes hands, which domains routinely do — a
registration lapses, a company is acquired, a contract ends.

At that point MateMail is in a bad state and cannot get out of it:

- the former tenant still holds the VERIFIED row, so mail for the domain still
  flows to their mailboxes;
- the new owner adds the domain, publishes the TXT record, and their claim can
  never succeed, because the partial unique index gives the verified row to
  exactly one tenant;
- nothing anywhere notices.

The second point is what makes this urgent rather than merely untidy: the
correct owner has no self-service path at all, and the incorrect one is reading
mail intended for them.

## Why this only raises a flag

The obvious response — revoke verification and deprovision — is the wrong one.
The signal is the absence of a DNS record, and DNS is absent for many reasons
that are not "this domain changed hands": a resolver outage, a registrar
migration, a customer tidying up records they were told were one-time. Cutting
off a legitimate customer's mail because of a week of bad lookups would do far
more damage, far more often, than the case being defended against.

So this task counts consecutive failures and surfaces them. A human decides
what a stale domain means, with the audit log and the customer in front of
them. `ownership_recheck_failures` resets to zero on any success, so only
sustained absence accumulates.
"""
import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Consecutive failures before a domain is treated as genuinely stale rather
#: than as a DNS hiccup. At one check per day this is roughly a week, which is
#: long enough to ride out a registrar migration and short enough that a real
#: change of ownership is caught while it still matters.
STALE_AFTER_FAILURES = 7


@shared_task(name="domains.reverify_ownership")
def reverify_domain_ownership():
    """
    Re-check the ownership TXT record of every verified domain.

    Never raises for a single domain's sake: one unresolvable name must not
    stop the sweep over all the others.
    """
    from apps.logs.models import LogEventType
    from apps.logs.utils import log_event
    from .models import Domain, DomainOwnership
    from .verification import check_ownership_dns

    domains = (
        Domain.objects.filter(ownership_status=DomainOwnership.VERIFIED)
        .select_related("tenant")
        .order_by("verification_last_checked_at")
    )

    checked = stale = 0
    now = timezone.now()

    for domain in domains:
        checked += 1
        try:
            found, message, technical = check_ownership_dns(domain)
        except Exception:
            # A resolver failure is not evidence of anything. Log it and leave
            # the counter alone rather than accumulating toward "stale" on the
            # strength of our own outage.
            logger.exception(
                "Ownership re-check could not run for %s — not counted as a failure",
                domain.domain,
            )
            continue

        if found:
            # Only write when something actually changes. A no-op UPDATE on
            # every verified domain every day is pure write amplification.
            if domain.ownership_recheck_failures or domain.verification_last_error:
                Domain.objects.filter(pk=domain.pk).update(
                    ownership_recheck_failures=0,
                    verification_last_error="",
                    verification_last_checked_at=now,
                )
            else:
                Domain.objects.filter(pk=domain.pk).update(
                    verification_last_checked_at=now
                )
            continue

        failures = domain.ownership_recheck_failures + 1
        Domain.objects.filter(pk=domain.pk).update(
            ownership_recheck_failures=failures,
            verification_last_checked_at=now,
            verification_last_error=message,
        )

        if failures == STALE_AFTER_FAILURES:
            # Audited once, at the crossing, not on every run afterwards —
            # otherwise the log fills with the same fact daily and the crossing
            # itself becomes impossible to find.
            stale += 1
            logger.error(
                "Domain %s (tenant %s) has failed ownership re-verification %s times "
                "in a row. Its verification TXT record is gone. If this domain has "
                "changed hands the current owner cannot claim it until this record "
                "is released. Needs a human decision.",
                domain.domain, domain.tenant_id, failures,
            )
            log_event(
                domain.tenant,
                LogEventType.DOMAIN_OWNERSHIP_STALE,
                result="failed",
                domain=domain,
                metadata={
                    "domain": domain.domain,
                    "consecutive_failures": failures,
                    "detail": technical or "verification record not found",
                },
            )

    logger.info(
        "Ownership re-verification swept %s verified domain(s); %s newly stale",
        checked, stale,
    )
    return {"checked": checked, "newly_stale": stale}
