import logging

from apps.mail_engine.dto import MailboxSpec
from apps.mail_engine.factory import get_adapter

logger = logging.getLogger(__name__)


def sync_team_box(team_box) -> None:
    """
    Reconcile a TeamBox with the Mail Engine.

    TeamBoxes have storage and delivery but no direct credential. Sender
    authorization is derived from active TeamBox grants with Send As or Send on
    behalf permission and travels in the same desired-state spec.
    """
    spec = MailboxSpec.from_model(team_box)
    get_adapter().ensure_mailbox(spec, "")
    team_box.mail_engine_provisioned = True
    team_box.mail_engine_error = ""
    team_box.save(
        update_fields=[
            "mail_engine_provisioned",
            "mail_engine_error",
            "updated_at",
        ]
    )


def mark_sync_failure(team_box, exc) -> None:
    from apps.mail_engine.errors import MailEngineError

    if isinstance(exc, MailEngineError):
        message = exc.customer_message
        logger.error("TeamBox sync failed for %s: %s", team_box.email, exc.log_message)
    else:
        message = MailEngineError.customer_message
        logger.exception("Unexpected TeamBox sync failure for %s", team_box.email)

    team_box.mail_engine_error = message
    team_box.save(update_fields=["mail_engine_error", "updated_at"])
