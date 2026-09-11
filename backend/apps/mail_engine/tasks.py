"""
Asynchronous Mail Engine operations.

Every task here relies on the port's idempotency contract: Celery may run a task
more than once, and a retried `ensure_*` or `delete_*` must converge rather than
duplicate. See `adapter.py` for the per-method guarantees.

Tasks take primitives (ids, addresses), never model instances, because a Celery
payload must be JSON-serializable and a model can change between enqueue and run.
"""
import logging

from celery import shared_task

from .errors import EngineUnavailable, MailEngineError

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=120,
    name="mail_engine.provision_domain",
)
def provision_domain_task(self, domain_id: str):
    """
    Bring a domain to its desired state in the Mail Engine.

    Safe to retry: `ensure_domain` is an idempotent upsert. Only transport
    failures are retried — an explicit rejection will not succeed on a retry, so
    it is recorded for the customer and the task stops.
    """
    from apps.domains.models import Domain

    from .dto import DomainSpec
    from .factory import get_adapter

    try:
        domain = Domain.objects.select_related("tenant").get(pk=domain_id)
    except Domain.DoesNotExist:
        logger.warning("provision_domain_task: domain %s no longer exists", domain_id)
        return

    # GATE (task boundary): fail closed regardless of which caller enqueued
    # this. A view-only check could be bypassed by any future code path that
    # calls .delay() directly, so the refusal lives here too — and this is the
    # last place before the adapter is reached.
    from apps.domains.verification import DomainNotVerified, assert_provisionable

    try:
        assert_provisionable(domain)
    except DomainNotVerified as exc:
        logger.error(
            "REFUSED provisioning unverified domain %s (tenant %s) — %s",
            domain.domain, domain.tenant_id, exc.technical_detail,
        )
        domain.mail_engine_error = exc.customer_message
        domain.save(update_fields=["mail_engine_error"])
        # Deliberately not retried: ownership will not appear by waiting.
        return

    plan = None
    try:
        from apps.billing.utils import get_plan

        plan = get_plan(domain.tenant)
    except Exception:
        # Plan lookup is an optimisation for engine limits, not a correctness
        # requirement; fall back to spec defaults rather than failing the task.
        logger.warning("provision_domain_task: could not resolve plan for %s", domain.domain)

    spec = DomainSpec.from_model(domain, plan=plan)

    try:
        get_adapter().ensure_domain(spec)
    except EngineUnavailable as exc:
        logger.warning("Domain provisioning deferred for %s: %s", domain.domain, exc.log_message)
        domain.mail_engine_error = exc.customer_message
        domain.save(update_fields=["mail_engine_error"])
        raise self.retry(exc=exc)
    except MailEngineError as exc:
        # A rejection is terminal: retrying an invalid request wastes attempts.
        logger.error("Domain provisioning failed for %s: %s", domain.domain, exc.log_message)
        domain.mail_engine_error = exc.customer_message
        domain.save(update_fields=["mail_engine_error"])
        return

    # DKIM is part of provisioning, not an optional extra. A domain marked
    # provisioned without it would be advertised to the customer as ready while
    # being unable to sign a single message — and unsigned mail from a new
    # domain is how a sending reputation is destroyed before it exists. This
    # raises on failure, so the lines below are not reached.
    _adopt_engine_dkim(domain, task=self)

    domain.mail_engine_provisioned = True
    domain.mail_engine_error = ""
    domain.save(update_fields=["mail_engine_provisioned", "mail_engine_error"])
    logger.info("Domain %s provisioned in the Mail Engine", domain.domain)


def _adopt_engine_dkim(domain, *, task=None) -> None:
    """
    Record the engine's public DKIM material for `domain` (DEC-007r).

    **Fails closed.** Successful DKIM adoption is part of successful
    provisioning: this raises rather than returning when the material cannot be
    obtained, so `mail_engine_provisioned` is never set behind a domain that
    cannot sign mail.

    Generation happens **only when the engine holds no key**. That condition is
    the whole safety property: `rotate_dkim_key` is not idempotent — every call
    mints a new pair and invalidates the DNS record the customer has already
    published. Calling it unconditionally from a retryable task would silently
    break DKIM for a working domain on every retry. So: read first, generate
    only on absence, never replace.

    Transient failures reuse the caller's retry semantics; an explicit
    rejection is terminal, exactly as for `ensure_domain`.
    """
    from .factory import get_adapter

    adapter = get_adapter()
    try:
        info = adapter.get_dkim_public_key(domain.domain)
        if info is None:
            info = adapter.rotate_dkim_key(domain.domain, selector=domain.dkim_selector)
            logger.info("Engine generated a DKIM key for %s", domain.domain)

        # Raised inside the try on purpose: an engine that answers without
        # usable material is a failure like any other, and must reach the same
        # handlers below. Validating after the try would give this one case its
        # own error path — and it would be the path that forgets to record
        # mail_engine_error, leaving the customer with a domain that is not
        # provisioned and no explanation of why.
        if not info or not info.public_key:
            raise MailEngineError(
                "engine returned no DKIM public key", operation="adopt_engine_dkim"
            )
    except EngineUnavailable as exc:
        logger.warning(
            "DKIM material unavailable for %s: %s", domain.domain, exc.log_message
        )
        domain.mail_engine_error = exc.customer_message
        domain.save(update_fields=["mail_engine_error"])
        if task is not None:
            raise task.retry(exc=exc)
        raise
    except MailEngineError as exc:
        logger.error(
            "DKIM adoption failed for %s: %s", domain.domain, exc.log_message
        )
        domain.mail_engine_error = exc.customer_message
        domain.save(update_fields=["mail_engine_error"])
        raise

    # Public material only. DkimKeyInfo has no field that can carry a private
    # key, and the adapter strips private fields on read — see
    # tests/test_engine_capabilities.py.
    domain.dkim_selector = info.selector
    domain.dkim_public_key = info.public_key
    domain.save(update_fields=["dkim_selector", "dkim_public_key"])


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    name="mail_engine.deprovision_domain",
)
def deprovision_domain_task(self, domain_name: str):
    """
    Remove a domain from the Mail Engine, signing key first.

    **The DKIM key is the security-relevant part of this task**, not the domain
    record. Removing a domain does not remove its key — measured against the
    real engine in P4B — and the surviving key is adopted by whoever registers
    that domain name next. A domain legitimately changes hands; the previous
    holder's private signing key must not travel with it.

    Order is deactivate → delete DKIM → delete domain:

    - deactivating first means the domain never sits in the window where it
      still accepts mail but can no longer sign it;
    - deleting the key before the domain means the dangerous artifact goes
      first, so a failure partway through leaves the *recoverable* problem
      (an orphaned domain record) rather than the dangerous one.

    Idempotent throughout, so a retry after a partial failure is safe. A
    domain that is already gone does **not** short-circuit the key deletion:
    that combination — no domain, stale key — is exactly the state this task
    exists to clean up, and it is the state a previously failed run leaves
    behind.
    """
    from .factory import get_adapter

    adapter = get_adapter()

    try:
        # Best-effort hardening, not the security property.
        #
        # Deliberately tolerant of ANY engine rejection. Asked to deactivate a
        # domain it does not hold, the engine answers `domain_invalid` — which
        # classifies as Rejected, not NotFound, because "invalid" legitimately
        # also covers a malformed name. Catching only NotFound here (the obvious
        # guess, and what this did first) meant an already-absent domain aborted
        # the task before the DKIM key was deleted: precisely the state this
        # task exists to clean up, and precisely the state a previously failed
        # run leaves behind.
        #
        # Verified against the live engine rather than inferred. EngineUnavailable
        # is re-raised because a transport failure is a real failure and belongs
        # on the retry path below.
        try:
            adapter.set_domain_active(domain_name, False)
        except EngineUnavailable:
            raise
        except MailEngineError as exc:
            logger.info(
                "Could not deactivate %s before removal (continuing to key "
                "deletion, which is the part that matters): %s",
                domain_name, exc.log_message,
            )

        # Unconditional. Not guarded by "did the domain exist", because the
        # whole point is that the key outlives the domain.
        adapter.delete_dkim_key(domain_name)
        adapter.delete_domain(domain_name)
    except EngineUnavailable as exc:
        logger.warning("Domain removal deferred for %s: %s", domain_name, exc.log_message)
        raise self.retry(exc=exc)
    except MailEngineError as exc:
        # Deliberately raises rather than returning. A silent return here would
        # report clean removal while a usable signing key for a domain MateMail
        # no longer controls stayed in the engine. Let the task fail visibly.
        logger.error(
            "Domain removal FAILED for %s — a DKIM signing key may remain in the "
            "engine and would be inherited by the next owner of this domain: %s",
            domain_name, exc.log_message,
        )
        raise

    logger.info(
        "Domain %s and its DKIM signing key removed from the Mail Engine", domain_name
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    name="mail_engine.deprovision_mailbox",
)
def deprovision_mailbox_task(self, address: str):
    """Remove a mailbox from the Mail Engine. Idempotent, as above."""
    from .factory import get_adapter

    try:
        get_adapter().delete_mailbox(address)
    except EngineUnavailable as exc:
        logger.warning("Mailbox removal deferred for %s: %s", address, exc.log_message)
        raise self.retry(exc=exc)
    except MailEngineError as exc:
        logger.error("Mailbox removal failed for %s: %s", address, exc.log_message)
        return
    logger.info("Mailbox %s removed from the Mail Engine", address)
