import logging

from apps.mail_engine.dto import MailboxSpec
from apps.mail_engine.factory import get_adapter

logger = logging.getLogger(__name__)


def sync_delegated_mailbox(mailbox) -> None:
    """
    Reconcile one personal mailbox's delegated SMTP sender authorization.

    Storage, password and direct login remain owned by the target mailbox.
    ensure_mailbox() receives no password, so the Native Engine preserves the
    existing credential while replacing only desired mailbox state and sender
    authorization.
    """
    get_adapter().ensure_mailbox(MailboxSpec.from_model(mailbox), "")


def grant_has_sender_right(grant) -> bool:
    return bool(grant.can_send_as or grant.can_send_on_behalf)
