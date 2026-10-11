"""Tenant-safe primary domain preference for newly created mailboxes.

Primary is not an email routing setting. DNS health can fluctuate without
silently switching the customer's selected default. Only newly promoted
domains must be verified and DNS-active.
"""
from django.db import transaction

from apps.tenants.models import Tenant
from .models import Domain, DomainOwnership, DomainStatus


def _eligible(tenant_id):
    return Domain.objects.filter(
        tenant_id=tenant_id,
        ownership_status=DomainOwnership.VERIFIED,
        status=DomainStatus.ACTIVE,
    )


@transaction.atomic
def choose_initial_primary(tenant_id):
    """Select the oldest eligible domain only when no primary exists.

    Locking Tenant serializes this with explicit selection and concurrent DNS
    checks. A missing eligible domain leaves the preference unset.
    """
    Tenant.objects.select_for_update().get(pk=tenant_id)
    if Domain.objects.filter(tenant_id=tenant_id, is_primary=True).exists():
        return
    candidate = _eligible(tenant_id).order_by("added_at", "id").first()
    if candidate:
        Domain.objects.filter(pk=candidate.pk).update(is_primary=True)


@transaction.atomic
def set_primary_domain(tenant_id, domain_id):
    """Return (domain, error_code) without leaking cross-tenant domains."""
    Tenant.objects.select_for_update().get(pk=tenant_id)
    domain = Domain.objects.filter(tenant_id=tenant_id, pk=domain_id).first()
    if domain is None:
        return None, "missing"
    if domain.ownership_status != DomainOwnership.VERIFIED or domain.status != DomainStatus.ACTIVE:
        return None, "ineligible"
    if not domain.is_primary:
        Domain.objects.filter(tenant_id=tenant_id, is_primary=True).update(is_primary=False)
        Domain.objects.filter(pk=domain.pk).update(is_primary=True)
        domain.is_primary = True
    return domain, None
