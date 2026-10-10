"""Authoritative, tenant-scoped, one-way Hub onboarding completion.

Domain health and ownership are independent. Only a verified, DNS-ready domain
plus an existing mailbox completes setup. After this is recorded, status is
sticky per organization even if its DNS later fails or a mailbox is removed.
"""
from django.utils import timezone

from apps.domains.models import Domain, DomainOwnership, DomainStatus
from apps.mailboxes.models import Mailbox

from .models import Tenant


def current_setup_steps(tenant_id):
    domains = Domain.objects.filter(tenant_id=tenant_id)
    return {
        "workspace_created": True,
        "domain_added": domains.exists(),
        "dns_verified": domains.filter(
            ownership_status=DomainOwnership.VERIFIED,
            status=DomainStatus.ACTIVE,
        ).exists(),
        "first_mailbox_created": Mailbox.objects.filter(tenant_id=tenant_id).exists(),
    }


def try_record_onboarding_completion(tenant_id, steps=None):
    """Idempotent, concurrency-safe latch. Never clears an existing timestamp."""
    # This early guard avoids extra domain/mailbox reads for established tenants.
    if not Tenant.objects.filter(
        id=tenant_id, onboarding_completed_at__isnull=True
    ).exists():
        return False

    if steps is None:
        steps = current_setup_steps(tenant_id)

    if not all(steps.values()):
        return False

    return bool(
        Tenant.objects.filter(
            id=tenant_id, onboarding_completed_at__isnull=True
        ).update(onboarding_completed_at=timezone.now())
    )


def onboarding_status(tenant):
    """Return live steps AND the permanent organization completion state."""
    steps = current_setup_steps(tenant.pk)
    try_record_onboarding_completion(tenant.pk, steps=steps)
    tenant.refresh_from_db(fields=["onboarding_completed_at"])
    return {
        **steps,
        "completed": tenant.onboarding_completed_at is not None,
        "completed_at": (
            tenant.onboarding_completed_at.isoformat()
            if tenant.onboarding_completed_at
            else None
        ),
    }
