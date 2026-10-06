"""
Sending mail from PostBox.

THE ORDER OF OPERATIONS IS THE DESIGN
    submit to Postfix  →  only then append to Sent

    Appending first would put messages in Sent that were never sent. That is
    the same class of lie as a backup that reports success without writing an
    archive: the copy in Sent is what a person checks when they ask "did that
    go?", so it must mean the mail server accepted it.

    The reverse failure — accepted by Postfix but the Sent copy fails — is
    real but far less harmful: the mail went, and the person is told the copy
    could not be filed rather than that the send failed.

WHAT POSTBOX DOES NOT GET TO DECIDE
    Who may appear in `From`. The list comes from authoritative MateMail data
    and nothing else. PostBox submits through the same authenticated path the
    rest of MateMail uses, so the engine's sender-authorisation policy, the
    per-mailbox rate limit, the organization's outbound switch and its
    suspension all still apply. There is no PostBox bypass, and this module
    does not re-implement any of those checks — it would only be a second,
    weaker copy.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from django.conf import settings

from apps.aliases.models import Alias, AliasStatus
from apps.mail_directory.models import AccessGrantKind, MailboxAccessGrant
from apps.mailboxes.models import Mailbox, MailboxKind

from .mime import clean_header

logger = logging.getLogger(__name__)


class SendFailed(Exception):
    """Submission was refused. Carries a customer message and an operator one."""

    def __init__(self, customer_message: str, log_message: str = ""):
        super().__init__(customer_message)
        self.customer_message = customer_message
        self.log_message = log_message or customer_message


@dataclass(frozen=True)
class Identity:
    address: str
    name: str = ""
    is_primary: bool = False
    kind: str = "mailbox"   # mailbox | alias | team_box
    send_mode: str = "send_as"  # send_as | on_behalf


def allowed_identities(mailbox: Mailbox) -> list[Identity]:
    """
    Every address this mailbox may legitimately send as.

    Two sources, both authoritative:

      * the mailbox's own address;
      * aliases that resolve to it and are active.

    Nothing else. In particular a PostBox user cannot nominate an address:
    if it is not in this list the submission is refused before it reaches
    Postfix, and Postfix would refuse it again through
    `reject_sender_login_mismatch`. Two independent refusals is the point —
    this one produces a good error message, that one is the guarantee.
    """
    identities = [
        Identity(
            address=mailbox.email,
            name=mailbox.full_name or "",
            is_primary=True,
            kind="mailbox",
        )
    ]

    aliases = (
        Alias.objects.filter(
            tenant=mailbox.tenant,
            destination_mailbox=mailbox,
            status=AliasStatus.ACTIVE,
        )
        .values_list("source_address", flat=True)
    )
    for address in aliases:
        identities.append(
            Identity(address=address, name=mailbox.full_name or "", kind="alias")
        )

    return identities


def assert_may_send_as(mailbox: Mailbox, from_address: str) -> Identity:
    """The identity for this address, or a refusal. Never trusts the request."""
    wanted = (from_address or "").strip().lower()
    for identity in allowed_identities(mailbox):
        if identity.address.lower() == wanted:
            return identity
    logger.warning(
        "PostBox refused a send as %r from mailbox %s", from_address, mailbox.pk
    )
    raise SendFailed(
        "You cannot send from that address.",
        f"sender {from_address!r} is not an identity of {mailbox.email}",
    )


def assert_organization_may_send(mailbox: Mailbox) -> None:
    """
    The organization-level gate, read from the single authority.

    `Tenant.can_send_mail` already combines approval, status and the outbound
    kill switch. Re-deriving any part of it here would create a second opinion
    that drifts.
    """
    tenant = mailbox.tenant
    if tenant is None or not tenant.can_send_mail:
        raise SendFailed(
            "Sending is currently disabled for your organization.",
            f"tenant {getattr(tenant, 'pk', None)} may not send",
        )
    if mailbox.status != "active":
        raise SendFailed(
            "This mailbox cannot send mail.", f"mailbox {mailbox.pk} status={mailbox.status}"
        )


def submit(
    message: EmailMessage,
    *,
    mailbox: Mailbox,
    envelope_from: str,
    recipients: list[str],
) -> None:
    """
    Hand the message to Postfix over authenticated submission.

    PostBox must authenticate to submission as the mailbox whose session is
    sending. Using MateMail's platform sender credential here authenticates as
    noreply@mail.matemail.online, and Postfix correctly rejects a customer
    envelope sender with reject_sender_login_mismatch.

    We deliberately do not retain the mailbox password typed at PostBox login.
    Instead the same Dovecot master identity already used for PostBox IMAP
    access authenticates as mailbox*postbox. Dovecot resolves that master
    login to the target mailbox identity, so Postfix's authoritative
    sender-login map still enforces mailbox and alias ownership. No relay
    bypass is introduced and the platform sender credential is not reused.

    Recipients come from the envelope, which is why Bcc works: the header was
    never written, and the address is simply in the RCPT list.
    """
    host = getattr(settings, "EMAIL_HOST", "")
    port = int(getattr(settings, "EMAIL_PORT", 587))
    use_tls = bool(getattr(settings, "EMAIL_USE_TLS", True))
    timeout = int(getattr(settings, "POSTBOX_SMTP_TIMEOUT", 30))
    master_user = getattr(settings, "POSTBOX_MASTER_USER", "postbox")
    master_password = getattr(settings, "POSTBOX_MASTER_PASSWORD", "")
    separator = getattr(settings, "POSTBOX_MASTER_SEPARATOR", "*")

    if not host:
        raise SendFailed(
            "Sending is not configured. Please contact your administrator.",
            "EMAIL_HOST is unset — PostBox cannot submit mail.",
        )

    if not master_password:
        raise SendFailed(
            "Sending is not configured. Please contact your administrator.",
            "POSTBOX_MASTER_PASSWORD is unset — PostBox cannot authenticate submission.",
        )

    if not recipients:
        raise SendFailed("Add at least one recipient.")

    auth_user = f"{mailbox.email}{separator}{master_user}"

    try:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(auth_user, master_password)
            smtp.send_message(message, from_addr=envelope_from, to_addrs=recipients)
    except smtplib.SMTPRecipientsRefused as exc:
        refused = ", ".join(sorted(exc.recipients)) if exc.recipients else ""
        raise SendFailed(
            f"The server refused these recipients: {refused}." if refused
            else "The server refused the recipients.",
            f"recipients refused: {exc.recipients!r}",
        ) from exc
    except smtplib.SMTPSenderRefused as exc:
        raise SendFailed(
            "The server refused that sender address.",
            f"sender refused: {exc.smtp_code} {exc.smtp_error!r}",
        ) from exc
    except smtplib.SMTPResponseException as exc:
        temporary = 400 <= int(exc.smtp_code or 0) < 500
        raise SendFailed(
            "The mail server is temporarily unavailable. Your message was not sent."
            if temporary
            else "The mail server rejected this message.",
            f"SMTP {exc.smtp_code}: {exc.smtp_error!r}",
        ) from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise SendFailed(
            "Your message could not be sent. Please try again.",
            f"submission failed: {exc!r}",
        ) from exc

def envelope_recipients(to: list[str], cc: list[str], bcc: list[str]) -> list[str]:
    """
    Every address that receives the message, deduplicated.

    Bcc is here and nowhere else. A `Bcc:` header would be delivered to all
    recipients and disclose exactly the people it exists to hide, which is why
    `build_message` refuses to write one.
    """
    seen: set[str] = set()
    result: list[str] = []
    for address in (*to, *cc, *bcc):
        cleaned = clean_header(address or "").strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result
