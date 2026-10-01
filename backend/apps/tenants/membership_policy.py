from apps.domains.models import DomainOwnership
from .models import MemberStatus, TenantMembership


def email_domain(email: str) -> str:
    """Return a normalized domain part for a validated email address."""
    return email.rsplit("@", 1)[-1].strip().rstrip(".").lower()


def tenant_allows_member_email(tenant, email: str) -> bool:
    """
    Organization users must use a domain whose ownership this tenant has proved.

    A merely-added/pending claim is not enough: otherwise a tenant could type a
    domain it does not control and invite users from it.
    """
    domain = email_domain(email)
    if not domain:
        return False
    return tenant.domains.filter(
        domain__iexact=domain,
        ownership_status=DomainOwnership.VERIFIED,
    ).exists()


def user_has_other_active_tenant(user, tenant) -> bool:
    """Defense in depth for legacy data and domain ownership transfers."""
    return TenantMembership.objects.filter(
        user=user,
        status=MemberStatus.ACTIVE,
    ).exclude(tenant=tenant).exists()


MEMBER_DOMAIN_ERROR = (
    "Team members must use an email address on a verified domain registered "
    "to this organization."
)

SINGLE_ORGANIZATION_ERROR = "This account is already assigned to an organization."
