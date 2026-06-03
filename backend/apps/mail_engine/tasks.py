import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=120,
    name="mail_engine.provision_domain",
)
def provision_domain_task(self, domain_id: str):
    """
    Async domain provisioning — creates domain + DKIM in the mail engine.
    Safe to queue (no passwords). Retries up to 5× with 2-minute backoff.
    """
    from apps.domains.models import Domain
    from apps.mail_engine.factory import get_adapter

    try:
        domain = Domain.objects.select_related("tenant").get(pk=domain_id)
    except Domain.DoesNotExist:
        return

    if domain.mail_engine_provisioned:
        return

    adapter = get_adapter()
    result = adapter.provision_domain(domain)

    if result.success:
        domain.mail_engine_provisioned = True
        domain.mail_engine_error = ""
        domain.save(update_fields=["mail_engine_provisioned", "mail_engine_error"])
        logger.info("Domain %s provisioned in mail engine", domain.domain)
    else:
        error = result.message[:500]
        domain.mail_engine_error = error
        domain.save(update_fields=["mail_engine_error"])
        logger.warning("Domain %s provisioning failed: %s", domain.domain, error)
        raise self.retry(exc=Exception(error))


@shared_task(name="mail_engine.deprovision_domain")
def deprovision_domain_task(domain_name: str):
    """Delete a domain from the mail engine. Called when domain is deleted from MateMail."""
    from apps.mail_engine.factory import get_adapter

    class _FakeDomain:
        domain = domain_name

    adapter = get_adapter()
    result = adapter.delete_domain(_FakeDomain())
    if not result.success:
        logger.warning("Failed to delete domain %s from mail engine: %s", domain_name, result.message)


@shared_task(name="mail_engine.deprovision_mailbox")
def deprovision_mailbox_task(email: str):
    """Delete a mailbox from the mail engine. Called when mailbox is deleted from MateMail."""
    from apps.mail_engine.factory import get_adapter

    class _FakeMailbox:
        pass

    mb = _FakeMailbox()
    mb.email = email  # type: ignore[attr-defined]

    adapter = get_adapter()
    result = adapter.delete_mailbox(mb)
    if not result.success:
        logger.warning("Failed to delete mailbox %s from mail engine: %s", email, result.message)
