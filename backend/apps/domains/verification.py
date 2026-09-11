"""
Domain ownership verification.

A customer proves control of a domain by publishing a TXT record:

    _matemail-verify.<domain>   IN TXT   "<token>"

Until that record resolves, the domain is PENDING and **cannot be provisioned
into any mail engine**. That is the P3 invariant: without it, anyone could type
a competitor's domain into MateMail and have the platform start accepting mail
for it.

Design notes:

- The token comes from `secrets.token_urlsafe`. Not a hash of the domain, not a
  uuid4, not a counter — those are guessable or enumerable, and a guessable
  token means the challenge proves nothing.
- The token is stable across retries. A customer whose DNS has not propagated
  yet must be able to retry without editing their zone again. Rotation is
  explicit and separate.
- Matching is exact. A TXT record that merely *contains* the token would let a
  wildcard or catch-all TXT satisfy the challenge.
- Exclusivity is enforced by a partial unique index, not by a Python check.
  See Domain.Meta.
"""
from __future__ import annotations

import logging
import secrets

import dns.exception
import dns.resolver
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Domain, DomainOwnership

logger = logging.getLogger(__name__)

#: Prefix makes the record self-identifying in a zone file dump, so an operator
#: reading someone's DNS can tell what put it there.
TOKEN_PREFIX = "matemail-verify"
_TOKEN_BYTES = 32
_DNS_TIMEOUT = 5.0

RECORD_TYPE = "TXT"


class OwnershipError(Exception):
    """Base for ownership failures. `customer_message` is safe to display."""

    customer_message = "Domain ownership could not be verified."

    def __init__(self, customer_message: str = "", technical_detail: str = ""):
        if customer_message:
            self.customer_message = customer_message
        self.technical_detail = technical_detail
        super().__init__(self.customer_message)

    def __str__(self) -> str:
        return self.customer_message


class DomainNotVerified(OwnershipError):
    """
    Raised by the provisioning gate. This is the exception that stands between
    an unverified domain and the Mail Engine.
    """

    customer_message = (
        "This domain has not been verified yet. Add the verification TXT record "
        "shown on the domain page, then run the check again."
    )


class DomainAlreadyOwned(OwnershipError):
    customer_message = (
        "This domain is already verified by another MateMail workspace. "
        "If that is not expected, contact support."
    )


# ── Tokens ──────────────────────────────────────────────────────────────────


def generate_verification_token() -> str:
    """A fresh, unguessable verification token."""
    return f"{TOKEN_PREFIX}-{secrets.token_urlsafe(_TOKEN_BYTES)}"


def ensure_verification_token(domain: Domain) -> str:
    """
    Return the domain's token, creating one if absent.

    Idempotent on purpose: calling this while a customer waits for propagation
    must not change the value they already pasted into their zone.
    """
    if not domain.verification_token:
        domain.verification_token = generate_verification_token()
        domain.save(update_fields=["verification_token", "updated_at"])
    return domain.verification_token


def rotate_verification_token(domain: Domain) -> str:
    """
    Issue a new token and return the domain to PENDING.

    For a token believed exposed, or a re-claim after a transfer. Deliberately
    explicit: rotation invalidates the record the customer already published,
    so it must never happen as a side effect of a retry.
    """
    domain.verification_token = generate_verification_token()
    domain.ownership_status = DomainOwnership.PENDING
    domain.ownership_verified_at = None
    domain.verification_last_error = ""
    domain.save(
        update_fields=[
            "verification_token",
            "ownership_status",
            "ownership_verified_at",
            "verification_last_error",
            "updated_at",
        ]
    )
    logger.info("Verification token rotated for domain %s", domain.domain)
    return domain.verification_token


# ── DNS lookup ──────────────────────────────────────────────────────────────


def _lookup_txt(record_name: str) -> tuple[list[str], str]:
    """
    Resolve TXT records. Returns (values, technical_error).

    Never raises: a lookup failure is an expected outcome of this check, not an
    exception. The technical string is for logs only.
    """
    try:
        answers = dns.resolver.resolve(record_name, RECORD_TYPE, lifetime=_DNS_TIMEOUT)
    except dns.resolver.NXDOMAIN:
        return [], "NXDOMAIN"
    except dns.resolver.NoAnswer:
        return [], "no TXT answer"
    except dns.resolver.NoNameservers as exc:
        return [], f"no nameservers: {exc}"
    except dns.exception.Timeout:
        return [], "timeout"
    except dns.exception.DNSException as exc:
        return [], f"{type(exc).__name__}: {exc}"

    values = []
    for rdata in answers:
        # A TXT value longer than 255 bytes arrives as multiple strings that
        # must be concatenated, not treated as separate records.
        try:
            values.append(b"".join(rdata.strings).decode("utf-8", errors="replace"))
        except AttributeError:
            values.append(str(rdata).strip('"'))
    return values, ""


def check_ownership_dns(domain: Domain) -> tuple[bool, str, str]:
    """
    Look for the expected TXT record.

    Returns (found, customer_message, technical_detail). Performs no writes, so
    it is safe to call from a read path or a task.
    """
    token = domain.verification_token
    if not token:
        return False, DomainNotVerified.customer_message, "no token issued"

    values, technical = _lookup_txt(domain.verification_record_name)

    if technical:
        if technical == "NXDOMAIN" or technical == "no TXT answer":
            return (
                False,
                (
                    f"No verification record found at {domain.verification_record_name}. "
                    "DNS changes can take up to an hour to propagate — add the record "
                    "and try again shortly."
                ),
                technical,
            )
        return (
            False,
            (
                "We could not read DNS for this domain just now. This is usually "
                "temporary — please try again in a few minutes."
            ),
            technical,
        )

    # Exact match. A record that merely contains the token would let a
    # catch-all or wildcard TXT satisfy the challenge.
    if any(value.strip() == token for value in values):
        return True, "", ""

    return (
        False,
        (
            f"A TXT record exists at {domain.verification_record_name}, but its value "
            "does not match the one MateMail issued. Replace it with the exact value "
            "shown on the domain page."
        ),
        f"{len(values)} TXT value(s) present, none matching",
    )


# ── Verification (writes) ───────────────────────────────────────────────────


def verify_domain_ownership(domain: Domain) -> tuple[bool, str]:
    """
    Check DNS and, on success, claim the domain for this tenant.

    Returns (verified, customer_message). Safe to retry: a domain that is
    already verified short-circuits, and a failure records why without
    disturbing the token.

    Concurrency: the claim is a single UPDATE guarded by the partial unique
    index on (domain) WHERE ownership_status='verified'. If another tenant wins
    the race, the database rejects this transaction and the caller is told the
    domain is already owned. No application-level check could close that window.
    """
    if domain.is_ownership_verified:
        return True, "This domain is already verified."

    found, message, technical = check_ownership_dns(domain)
    now = timezone.now()

    if not found:
        # The token is deliberately NOT logged: it is the shared secret of the
        # challenge, and a log aggregator is a poor place to keep it.
        logger.info(
            "Ownership check failed for %s (tenant %s): %s",
            domain.domain, domain.tenant_id, technical or "no match",
        )
        Domain.objects.filter(pk=domain.pk).update(
            verification_last_checked_at=now,
            verification_last_error=message,
        )
        domain.verification_last_checked_at = now
        domain.verification_last_error = message
        return False, message

    try:
        with transaction.atomic():
            updated = Domain.objects.filter(
                pk=domain.pk, ownership_status=DomainOwnership.PENDING
            ).update(
                ownership_status=DomainOwnership.VERIFIED,
                ownership_verified_at=now,
                verification_last_checked_at=now,
                verification_last_error="",
            )
            if not updated:
                # Someone else moved this row first; re-read to report truthfully.
                domain.refresh_from_db()
                if domain.is_ownership_verified:
                    return True, "This domain is already verified."
                return False, DomainNotVerified.customer_message
    except IntegrityError:
        # The partial unique index rejected the claim: another tenant holds the
        # verified record for this name.
        logger.warning(
            "Ownership claim rejected for %s (tenant %s): already verified elsewhere",
            domain.domain, domain.tenant_id,
        )
        Domain.objects.filter(pk=domain.pk).update(
            verification_last_checked_at=now,
            verification_last_error=DomainAlreadyOwned.customer_message,
        )
        return False, DomainAlreadyOwned.customer_message

    domain.refresh_from_db()
    logger.info(
        "Domain %s verified for tenant %s", domain.domain, domain.tenant_id
    )
    return True, "Domain ownership verified."


# ── The provisioning gate ───────────────────────────────────────────────────


def assert_provisionable(domain: Domain) -> None:
    """
    Fail closed unless the domain's ownership is verified.

    Every path that can reach the Mail Engine calls this: the create view, the
    manual provision endpoint, the Celery task, and mailbox provisioning. It is
    enforced at the service/task boundary rather than only in a view, so a new
    caller cannot bypass it by not knowing about it.

    Raises DomainNotVerified.
    """
    if not domain.is_ownership_verified:
        raise DomainNotVerified(
            technical_detail=(
                f"domain={domain.domain} tenant={domain.tenant_id} "
                f"ownership={domain.ownership_status}"
            )
        )
