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

    domain.mail_engine_provisioned = True
    domain.mail_engine_error = ""
    domain.save(update_fields=["mail_engine_provisioned", "mail_engine_error"])
    logger.info("Domain %s provisioned in the Mail Engine", domain.domain)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    name="mail_engine.deprovision_domain",
)
def deprovision_domain_task(self, domain_name: str):
    """
    Remove a domain from the Mail Engine.

    Idempotent: deleting an already-absent domain succeeds, so a retry after a
    partial failure is safe.
    """
    from .factory import get_adapter

    try:
        get_adapter().delete_domain(domain_name)
    except EngineUnavailable as exc:
        logger.warning("Domain removal deferred for %s: %s", domain_name, exc.log_message)
        raise self.retry(exc=exc)
    except MailEngineError as exc:
        logger.error("Domain removal failed for %s: %s", domain_name, exc.log_message)
        return
    logger.info("Domain %s removed from the Mail Engine", domain_name)


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
