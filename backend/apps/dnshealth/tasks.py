from celery import shared_task


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name="dnshealth.check_domain_dns")
def check_domain_dns(self, domain_id: str):
    from apps.domains.models import Domain
    from .services import check_dns_for_domain

    try:
        domain = Domain.objects.get(pk=domain_id)
    except Domain.DoesNotExist:
        return

    try:
        check_dns_for_domain(domain)
    except Exception as exc:
        raise self.retry(exc=exc)


@shared_task(name="dnshealth.check_all_active_domains_dns")
def check_all_active_domains_dns():
    from apps.domains.models import Domain, DomainStatus
    from .services import check_dns_for_domain

    domains = Domain.objects.exclude(status=DomainStatus.PAUSED)
    for domain in domains:
        try:
            check_dns_for_domain(domain)
        except Exception:
            pass
