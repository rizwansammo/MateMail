"""
Which domains may receive MateMail client settings, and what the answer may say.

THE ONE RULE THAT MATTERS
    This endpoint answers about a DOMAIN. It must never answer about a MAILBOX.

    `alice@acme.example` and `nobody@acme.example` produce byte-identical
    responses, because the only thing consulted is `acme.example`. Whether
    `alice` exists is decided later, by Dovecot, against a password. An
    Autodiscover service that returned "not found" for an unknown local part
    would be a mailbox enumeration API with an XML wrapper, reachable by anyone
    on the internet with no credential — you could walk a customer's staff list
    from a laptop.

    That is why `settings_for()` takes the address only to echo it back as the
    login name, and never looks it up.

ELIGIBILITY IS THE EXISTING PROVISIONING GATE, NOT A NEW ONE
    A domain is eligible when MateMail already treats it as one it hosts:

      - ownership VERIFIED — the tenant proved control by publishing the TXT
        token. Without this anybody could add `microsoft.com` to a trial
        workspace and have us hand out settings for it.
      - status ACTIVE or WARNING — the domain is live. WARNING counts because
        it means some DNS check is imperfect (often DMARC), not that mail is
        broken, and a client being set up during that window is normal.
      - mail_engine_provisioned — the mailboxes actually exist somewhere.

    These are read from the current model rather than reimplemented. If the
    provisioning rules tighten later, this tightens with them.

WHAT AN ANSWER MAY CONTAIN
    Hostnames and ports that are already public in `docs/MAIL_CLIENT_SETUP.md`,
    and the address the caller already knew. Never a tenant id, tenant name,
    mailbox id, mailbox count, DKIM selector, provisioning state, backend
    hostname, or anything that differs between two eligible domains.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings


#: Deliberately permissive, and deliberately not RFC 5322. This decides whether
#: to *parse* a string as an address, not whether to deliver to it; a real
#: address that this rejects simply gets the same "no settings" answer as an
#: unknown domain, which is a bad client experience but never a disclosure.
#: Anchored, with no nested quantifier, so a long hostile string cannot make it
#: backtrack.
#: The trailing dot is optional and captured outside the group: a resolver
#: and a zone file disagree about whether an FQDN carries one, and a client
#: that sends the absolute form is not making a mistake.
_ADDRESS = re.compile(r"^[^@\s]{1,64}@([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                      r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)\.?$")


@dataclass(frozen=True)
class ClientSettings:
    """
    What a mail client needs, derived from configuration rather than written
    out here, so this cannot drift from what the mail engine actually serves.
    """

    login_name: str
    imap_server: str
    imap_port: int
    smtp_server: str
    smtp_port: int

    #: IMAP 993 is TLS from the first byte. SMTP 587 is not — it starts in the
    #: clear and upgrades with STARTTLS, and saying otherwise makes Outlook open
    #: a TLS handshake against a plaintext listener and fail with an error that
    #: names neither side. The two flags are separate for exactly this reason.
    imap_ssl: bool = True
    smtp_encryption: str = "TLS"


def parse_domain(address: str | None) -> str | None:
    """
    The domain of `address`, lowercased, or None if it is not an address.

    Length is capped before the regex runs: the pattern is linear, but there is
    no reason to hand a megabyte of text to it, and an address that long is not
    one.
    """
    if not address or len(address) > 320:
        return None
    match = _ADDRESS.match(address.strip())
    if not match:
        return None
    return match.group(1).lower().rstrip(".")


def hosted_domain(domain_name: str | None):
    """
    The Domain row MateMail hosts under this name, or None.

    Cross-tenant by necessity — the caller is an anonymous mail client and has
    no tenant. That is safe only because nothing about the row is returned; see
    `settings_for`, which ignores the row entirely and reads configuration.

    `.first()` rather than `.get()`: the same name existing twice would be a
    data problem, and raising here would turn it into a 500 for a client that
    only wanted a port number.
    """
    if not domain_name:
        return None

    from apps.domains.models import Domain, DomainOwnership, DomainStatus

    return (
        Domain.objects.filter(
            domain__iexact=domain_name,
            ownership_status=DomainOwnership.VERIFIED,
            status__in=(DomainStatus.ACTIVE, DomainStatus.WARNING),
            mail_engine_provisioned=True,
        )
        .only("id")
        .first()
    )


def settings_for(address: str) -> ClientSettings:
    """
    The settings for an eligible domain.

    Takes the address only to echo it as the login name — MateMail
    authenticates on the full address, so a client told to send `alice` alone
    would be refused by Dovecot with a password error that looks like a wrong
    password.

    Every other value comes from `MAIL_HOSTNAME`. Writing `mx.matemail.online`
    here as a literal would mean a deployment that changed its mail hostname
    kept handing out the old one, and the failure would appear as clients that
    cannot connect rather than as a wrong setting.
    """
    host = settings.MAIL_HOSTNAME
    return ClientSettings(
        login_name=address.strip(),
        imap_server=host,
        imap_port=993,
        smtp_server=host,
        smtp_port=587,
    )
