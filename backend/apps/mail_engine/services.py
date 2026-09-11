"""
Mail Engine operations that need product-level coordination.

Anything here does something the adapter alone cannot make safe: it holds a
lock, spans several engine calls, or has to leave MateMail's own records
consistent with the engine's afterwards.

Deliberately NOT Celery tasks. Celery retries, and every operation in this
module is one the port marks as non-idempotent — a retry would not repair a
failure, it would cause a second one. Callers invoke these synchronously from a
request or a management command and surface the typed error.
"""
import logging

from django.db import transaction

from .errors import MailEngineError

logger = logging.getLogger(__name__)


def rotate_domain_dkim(domain_id) -> "object":
    """
    Replace a domain's DKIM keypair and record the new public material.

    Returns the `DkimKeyInfo` the engine produced. Raises a `MailEngineError`
    subclass on failure, having recorded a customer-safe reason on the domain.

    **Serialized per domain.** Rotation is read → delete → add → read against a
    shared engine, and two concurrent rotations for the same domain interleave
    destructively: the second can delete the key the first just created, and
    both can then persist a public key the engine no longer holds. Every
    outgoing message for that domain would fail DKIM, with MateMail's database
    insisting the configuration was fine.

    `select_for_update()` on the domain row is the serialization point. It is
    the same pattern used for tenant-scoped counters elsewhere, and it is
    per-domain rather than global: rotating one customer's domain must not block
    another's.

    The transaction does hold a row lock across engine calls, which is normally
    worth avoiding. It is acceptable here because rotation is a rare, explicitly
    requested administrative action and the adapter's timeout bounds the hold to
    a few seconds. It must not be copied into a hot path.

    **Fails closed.** If the engine ends up without a readable key, the error
    propagates and `dkim_public_key` is left untouched rather than cleared —
    what is published in DNS stays visible to the operator while they
    investigate. MateMail never sees or stores the private half (DEC-007r).
    """
    from apps.domains.models import Domain

    from .factory import get_adapter

    try:
        with transaction.atomic():
            domain = Domain.objects.select_for_update().get(pk=domain_id)

            info = get_adapter().rotate_dkim_key(
                domain.domain, selector=domain.dkim_selector
            )

            if not info or not info.public_key or not info.selector:
                raise MailEngineError(
                    "engine returned unusable DKIM material after rotation",
                    operation="rotate_domain_dkim",
                )

            domain.dkim_selector = info.selector
            domain.dkim_public_key = info.public_key
            domain.mail_engine_error = ""
            domain.save(
                update_fields=["dkim_selector", "dkim_public_key", "mail_engine_error"]
            )
    except MailEngineError as exc:
        # Recorded OUTSIDE the atomic block, deliberately. Writing the reason
        # inside it and then re-raising rolls the write back along with
        # everything else, so the customer is left with a failed rotation and no
        # explanation anywhere — which is what the first version of this
        # function did, and what the tests caught.
        #
        # A queryset update rather than a model save: the instance may not exist
        # (the row lock failed) and this must run in its own transaction.
        logger.error("DKIM rotation failed for %s: %s", domain_id, exc.log_message)
        Domain.objects.filter(pk=domain_id).update(
            mail_engine_error=exc.customer_message
        )
        raise

    logger.info(
        "DKIM rotated for %s — the customer must republish %s",
        domain.domain, info.dns_record_name,
    )
    return info
