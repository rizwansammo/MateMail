"""
Transactional application email.

Four messages have to reach a customer before MateMail can host any mail at
all: verify your address, reset your password, you have been invited, and
security notices. They are sent through **MateMail's own Mail Engine** (DEC-013)
using a dedicated service identity, `noreply@mail.matemail.pro`, over
authenticated SMTP submission on `mx.matemail.pro:587` with STARTTLS.

An earlier version of this module said the opposite — that these must never
depend on MateMail's own engine and had to go through an external provider.
That was the pre-DEC-013 position and is no longer the architecture.

The concern behind it was real and is addressed rather than ignored: a platform
that cannot send a password reset while its own mail system is down is a
platform nobody can recover an account on. Two things separate the two failure
domains:

- the sending identity lives on `mail.matemail.pro`, a subdomain distinct
  from every customer domain, with its own SPF, DKIM and DMARC, so a customer
  who damages their own domain's reputation cannot take account recovery with
  them;
- `transactional_email_configured()` below refuses to pretend, and every send
  reports honestly whether it worked.

What remains true is that this path shares infrastructure with customer mail.
If the engine is down, account recovery is down. That is a monitoring and
alerting requirement (P7), not something this module can solve.

Two things were wrong before this module existed.

**`fail_silently=True` on every send.** Django swallows the exception and
returns 0. The API then answered "Verification email sent." having sent
nothing, and the only trace was the absence of an email nobody was watching
for. A transactional send that fails is an incident: the customer is stuck at a
verification wall they cannot pass.

**`EMAIL_HOST` defaulted to `localhost`.** Nothing listens on port 587 of the
application container, so a deployment that forgot to set the variable would
have silently discarded every message. The loopback check below is still the
right guard: the engine is reached by its own hostname across a private network,
never at localhost, so a loopback value can only ever mean "misconfigured".

The policy here: never claim delivery that did not happen. A send that fails is
logged with its cause, and the caller is told whether it worked so it can
answer the customer honestly.
"""
import logging
import uuid

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
            "hostname (mx.matemail.pro:587, STARTTLS) with the platform "
            "service credential in EMAIL_HOST_USER/EMAIL_HOST_PASSWORD — see "
            "docs/DEPLOYMENT.md and DEC-013.",
            purpose,
        )
        return False

    from_address = transactional_from_address()
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=from_address,
        to=recipients,
        connection=get_connection(fail_silently=False),
        # Django generates a Message-ID from the LOCAL hostname when one is not
        # supplied. In a container that is the container id, so real delivered
        # mail carried `<...@00a3f41e29d7>` — a public header advertising an
        # internal identifier, unstable across every deploy, and not a real
        # domain. Set it from our own sending domain instead.
        headers={"Message-ID": make_message_id(from_address)},
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


def message_id_domain(from_address: str = "") -> str:
    """
    The right-hand side for a Message-ID: the domain we actually send from.

    Taken from the From address, falling back to MAIL_HOSTNAME and then
    MAIL_DOMAIN. Never the local hostname — in a container that is an ephemeral
    id, which is both an information leak and not a real domain.
    """
    address = from_address or transactional_from_address()
    if "@" in address:
        # `MateMail <noreply@mail.matemail.pro>` -> mail.matemail.online
        domain = address.rsplit("@", 1)[1].strip().rstrip(">").strip()
        if domain:
            return domain
    return (
        getattr(settings, "MAIL_HOSTNAME", "")
        or getattr(settings, "MAIL_DOMAIN", "")
        or "localhost"
    )


def make_message_id(from_address: str = "") -> str:
    """
    A globally unique RFC 5322 Message-ID rooted at our own domain.

    Uniqueness comes from `uuid4` — 122 random bits, so collisions are not a
    practical concern even across every process and container that will ever
    run. Deliberately not a timestamp plus a counter: those collide across
    processes, which is precisely the situation here.
    """
    return f"<{uuid.uuid4()}@{message_id_domain(from_address)}>"


def transactional_from_address() -> str:
    """
    The From address for application mail.

    Comes from DEFAULT_FROM_EMAIL. It names MateMail — never the Mail Engine,
    never mailcow, never the provider. A customer must not be able to learn
    what runs underneath from a header they were sent.
    """
    return getattr(settings, "DEFAULT_FROM_EMAIL", "") or f"MateMail <noreply@{settings.MAIL_DOMAIN}>"
