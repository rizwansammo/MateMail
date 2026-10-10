"""Optional personal mailbox creation when an invited user joins MateMail.

Invitation stores intent, NEVER a password. Database changes are atomic;
external Mail Engine provisioning is separate and failures remain repairable.
"""
import logging
from django.db import IntegrityError
from apps.billing.utils import check_mailbox_limit, get_plan, resolve_mailbox_quota
from apps.domains.models import Domain
from apps.domains.verification import DomainNotVerified, assert_provisionable
from apps.logs.models import LogEventType
from apps.logs.utils import log_event
from apps.mail_directory.models import AddressClaim
from apps.mail_directory.services import AddressConflict
from apps.mailboxes.models import Mailbox
from apps.tenants.policy import MailNotPermitted, assert_can_use_mail

logger = logging.getLogger(__name__)


class InviteMailboxError(Exception):
    def __init__(self, message, status_code=409):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def preflight_invited_mailbox(tenant, email):
    """Server-side gate. Re-check under the tenant lock at accept/signup."""
    try:
        assert_can_use_mail(tenant)
    except MailNotPermitted as exc:
        raise InviteMailboxError(exc.customer_message, 403) from exc
    email = email.strip().lower()
    if email.count("@") != 1:
        raise InviteMailboxError("Invalid mailbox email.", 400)
    local_part, domain_name = email.rsplit("@", 1)
    if not local_part or len(local_part) > 64:
        raise InviteMailboxError("Mailbox local part must be 1–64 characters.", 400)
    domain = Domain.objects.for_tenant(tenant).filter(domain__iexact=domain_name).first()
    if not domain:
        raise InviteMailboxError("The email domain does not belong to this organization.", 400)
    try:
        assert_provisionable(domain)
    except DomainNotVerified as exc:
        raise InviteMailboxError(exc.customer_message) from exc
    if AddressClaim.objects.filter(address__iexact=email).exists():
        raise InviteMailboxError("That address already belongs to a mailbox, alias or group.")
    allowed, msg = check_mailbox_limit(tenant)
    if not allowed:
        raise InviteMailboxError(msg, 402)
    quota, quota_error = resolve_mailbox_quota(get_plan(tenant), None)
    if quota_error:
        raise InviteMailboxError(quota_error)
    return domain, local_part, quota


def reserve_invited_mailbox(tenant, invite, full_name):
    """Call INSIDE membership/invite DB transaction under locked tenant."""
    if not invite.create_mailbox:
        return None
    domain, local_part, quota = preflight_invited_mailbox(tenant, invite.email)
    try:
        return Mailbox.objects.create(
            tenant=tenant, domain=domain, local_part=local_part,
            full_name=full_name or invite.email.split("@")[0], quota_mb=quota,
        )
    except (AddressConflict, IntegrityError) as exc:
        raise InviteMailboxError("That mailbox address is already in use.") from exc


def complete_invited_mailbox(mailbox, password, *, request=None):
    """Provision after DB commit; never discard engine-side ambiguous state.

    If the external engine fails, the durable pending local mailbox is
    visible and can be re-provisioned using the existing administrator API.
    """
    from apps.mail_engine.errors import MailEngineError
    from apps.mailboxes.views import _provision_mailbox
    from apps.forward_groups.services import sync_all_groups_for_tenant
    try:
        _provision_mailbox(mailbox, password)
    except MailEngineError:
        pass  # The adapter helper saved a customer-safe status.
    except Exception:
        logger.exception("Invited mailbox engine error (id=%s)", mailbox.pk)
        mailbox.mail_engine_error = MailEngineError.customer_message
        mailbox.save(update_fields=["mail_engine_error"])
    try:
        sync_all_groups_for_tenant(mailbox.tenant)
    except Exception:
        logger.exception("Invited mailbox group sync error (id=%s)", mailbox.pk)
    log_event(
        mailbox.tenant, LogEventType.MAILBOX_CREATED, request=request,
        mailbox=mailbox, metadata={"origin": "team_invite"},
    )
    return {
        "id": str(mailbox.pk),
        "email": mailbox.email,
        "mail_service_ready": mailbox.mail_engine_provisioned,
        "mail_service_message": mailbox.mail_engine_error,
    }
