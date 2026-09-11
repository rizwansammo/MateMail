"""
Transactional application email.

Four messages have to reach a customer before MateMail can host any mail at
all: verify your address, reset your password, you have been invited, and
security notices. None of them can depend on MateMail's own Mail Engine — that
engine is not installed, and even once it is, a platform that cannot send a
password reset while its own mail system is down is a platform nobody can
recover an account on. These go out through an external provider over SMTP.

Two things were wrong before this module existed.

**`fail_silently=True` on every send.** Django swallows the exception and
returns 0. The API then answered "Verification email sent." having sent
nothing, and the only trace was the absence of an email nobody was watching
for. A transactional send that fails is an incident: the customer is stuck at a
verification wall they cannot pass.

**`EMAIL_HOST` defaulted to `localhost`.** Nothing listens on port 587 of the
application container, so a deployment that forgot to set the variable would
have silently discarded every message — and if something ever did listen, that
would be the Mail Engine, which is exactly where transactional mail must not go.

The policy here: never claim delivery that did not happen. A send that fails is
logged with its cause, and the caller is told whether it worked so it can
answer the customer honestly.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

logger = logging.getLogger(__name__)


class EmailNotConfigured(RuntimeError):
    """Raised when the deployment has no usable transactional mail path."""


def transactional_email_configured() -> bool:
    """
    True when this deployment can actually send.

    The console and locmem backends count as configured: they are the
    development and test paths and they genuinely deliver somewhere
    inspectable. The SMTP backend needs a host that is not this machine.
    """
    backend = getattr(settings, "EMAIL_BACKEND", "")
    if "smtp" not in backend:
        return True

    host = (getattr(settings, "EMAIL_HOST", "") or "").strip().lower()
    if not host or host in {"localhost", "127.0.0.1", "::1"}:
        return False
    return True


def send_transactional(
    *,
    subject: str,
    body: str,
    to: list[str] | str,
    html_body: str | None = None,
    purpose: str = "transactional",
) -> bool:
    """
    Send one transactional message. Returns True only if it was accepted.

    Never raises to the caller: a customer-facing view should not become a 500
    because a mail provider is having a bad afternoon. It returns False and
    logs the reason, and the caller decides what to tell the customer — which
    is the part that was missing when everything used `fail_silently=True`.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    if not recipients:
        return False

    if not transactional_email_configured():
        logger.error(
            "Transactional email (%s) NOT sent: EMAIL_HOST is unset or points at "
            "this machine. Set EMAIL_HOST to the Mail Engine's submission "
            "hostname (mx.matemail.online:587, STARTTLS) with the platform "
            "service credential in EMAIL_HOST_USER/EMAIL_HOST_PASSWORD — see "
            "docs/DEPLOYMENT.md and DEC-013.",
            purpose,
        )
        return False

    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=transactional_from_address(),
        to=recipients,
        connection=get_connection(fail_silently=False),
    )
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        sent = message.send(fail_silently=False)
    except Exception as exc:
        # Deliberately broad: SMTP failure modes span socket errors, TLS
        # errors, authentication errors and provider-specific refusals, and a
        # customer-facing view must not 500 on any of them. The exception is
        # logged in full rather than swallowed — the rule this project has
        # about `except Exception: pass` is about hiding failures, and this
        # hides nothing.
        logger.exception("Transactional email (%s) failed to send: %s", purpose, exc)
        return False

    if not sent:
        logger.error("Transactional email (%s) was not accepted by the server.", purpose)
        return False

    logger.info("Transactional email (%s) sent to %d recipient(s).", purpose, len(recipients))
    return True


def transactional_from_address() -> str:
    """
    The From address for application mail.

    Comes from DEFAULT_FROM_EMAIL. It names MateMail — never the Mail Engine,
    never mailcow, never the provider. A customer must not be able to learn
    what runs underneath from a header they were sent.
    """
    return getattr(settings, "DEFAULT_FROM_EMAIL", "") or f"MateMail <noreply@{settings.MAIL_DOMAIN}>"
