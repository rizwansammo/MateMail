from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from .models import AccessGrantKind, AddressClaim, AddressKind, MailboxAccessGrant


class AddressConflict(ValueError):
    customer_message = "That email address is already in use."

    def __init__(self, message=None):
        super().__init__(message or self.customer_message)
        self.customer_message = message or self.customer_message


def normalize_address(address: str) -> str:
    value = (address or "").strip().lower()
    try:
        validate_email(value)
    except ValidationError as exc:
        raise AddressConflict("Enter a valid email address.") from exc
    return value


def _existing_source_kind(address: str):
    """
    Defense-in-depth for rows created before/beside the registry.
    """
    from apps.aliases.models import Alias
    from apps.forward_groups.models import ForwardGroup
    from apps.mailboxes.models import Mailbox, MailboxKind

    mailbox = Mailbox.objects.filter(email__iexact=address).only("kind").first()
    if mailbox:
        return (
            AddressKind.TEAM_BOX
            if mailbox.kind == MailboxKind.TEAM_BOX
            else AddressKind.MAILBOX
        )
    if Alias.objects.filter(source_address__iexact=address).exists():
        return AddressKind.ALIAS
    if ForwardGroup.objects.filter(address__iexact=address).exists():
        return AddressKind.FORWARD_GROUP
    return None


def reserve_address(*, tenant, domain, address: str, kind: str) -> AddressClaim:
    """
    Atomically reserve an address across every MateMail recipient type.
    """
    normalized = normalize_address(address)

    if domain.tenant_id != tenant.id:
        raise AddressConflict("The email domain is not in this organization.")

    _, _, host = normalized.rpartition("@")
    if host != domain.domain.strip().lower():
        raise AddressConflict("The email address does not belong to that domain.")

    if AddressClaim.objects.filter(address__iexact=normalized).exists():
        raise AddressConflict()

    if _existing_source_kind(normalized):
        raise AddressConflict()

    try:
        with transaction.atomic():
            return AddressClaim.objects.create(
                tenant=tenant,
                domain=domain,
                address=normalized,
                kind=kind,
            )
    except IntegrityError as exc:
        raise AddressConflict() from exc


def release_address(address: str, *, expected_kind: str | None = None) -> bool:
    normalized = normalize_address(address)
    rows = AddressClaim.objects.filter(address__iexact=normalized)
    if expected_kind is not None:
        rows = rows.filter(kind=expected_kind)
    deleted, _ = rows.delete()
    return bool(deleted)


@dataclass(frozen=True)
class MailboxPermissions:
    can_read: bool = False
    can_manage: bool = False
    can_send_as: bool = False
    can_send_on_behalf: bool = False

    @classmethod
    def owner(cls):
        return cls(True, True, True, True)


def permissions_for(*, grantee_mailbox, target_mailbox) -> MailboxPermissions:
    if grantee_mailbox.pk == target_mailbox.pk:
        return MailboxPermissions.owner()

    if grantee_mailbox.tenant_id != target_mailbox.tenant_id:
        return MailboxPermissions()

    grant = (
        MailboxAccessGrant.objects
        .filter(
            tenant_id=target_mailbox.tenant_id,
            grantee_mailbox=grantee_mailbox,
            target_mailbox=target_mailbox,
            active=True,
        )
        .first()
    )
    if not grant:
        return MailboxPermissions()

    return MailboxPermissions(
        can_read=grant.can_read,
        can_manage=grant.can_manage,
        can_send_as=grant.can_send_as,
        can_send_on_behalf=grant.can_send_on_behalf,
    )


def grant_mailbox_access(
    *,
    target_mailbox,
    grantee_mailbox,
    grant_type: str,
    can_read: bool = True,
    can_manage: bool = False,
    can_send_as: bool = False,
    can_send_on_behalf: bool = False,
) -> MailboxAccessGrant:
    grant = MailboxAccessGrant(
        tenant=target_mailbox.tenant,
        target_mailbox=target_mailbox,
        grantee_mailbox=grantee_mailbox,
        grant_type=grant_type,
        can_read=can_read,
        can_manage=can_manage,
        can_send_as=can_send_as,
        can_send_on_behalf=can_send_on_behalf,
    )
    grant.save()
    return grant
