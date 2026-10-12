"""One global reservation namespace for platform and verified organization domains.

Only verified organization domains acquire a claim. Pending DNS requests remain
unowned, so multiple pending claims can coexist. Registry entries are durable:
customer domain deletion deliberately does not release one until the engine's
domain, DKIM key, and mailboxes have been independently verified absent.
"""
from django.db import transaction

from .models import MailDomainOwnership, MailDomainOwnerKind


class DomainRegistryConflict(ValueError):
    customer_message = (
        "This domain is reserved or owned by another MateMail account. "
        "Contact platform support if you need a transfer."
    )


def canonical_name(value):
    return (value or "").strip().rstrip(".").lower()


def ownership_for(domain_name):
    return MailDomainOwnership.objects.filter(domain=canonical_name(domain_name)).first()


def assert_available_for_organization(domain_name):
    claim = ownership_for(domain_name)
    if claim is not None:
        raise DomainRegistryConflict()
    return True


def claim_verified_domain(domain):
    """Claim atomically with DNS verification; never transfer a previous claim."""
    name = canonical_name(domain.domain)
    with transaction.atomic():
        claim, _ = MailDomainOwnership.objects.get_or_create(
            domain=name,
            defaults={
                "owner_kind": MailDomainOwnerKind.ORGANIZATION,
                "tenant_id": domain.tenant_id,
                "source_domain_id": domain.pk,
            },
        )
        if (
            claim.owner_kind != MailDomainOwnerKind.ORGANIZATION
            or claim.tenant_id != domain.tenant_id
            or claim.source_domain_id != domain.pk
        ):
            raise DomainRegistryConflict()
        return claim


def assert_registry_authorizes_domain(domain):
    claim = ownership_for(domain.domain)
    if (
        claim is None
        or claim.owner_kind != MailDomainOwnerKind.ORGANIZATION
        or claim.tenant_id != domain.tenant_id
        or claim.source_domain_id != domain.pk
    ):
        raise DomainRegistryConflict()
    return claim
