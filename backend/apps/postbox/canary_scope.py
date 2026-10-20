"""Mailbox-scoped PostBox MFA canary release boundary.

The global MFA/SMTP gates may be enabled for a limited acceptance test without
changing password verification or protocol policies for unrelated mailboxes.
This is an *additional* scope restriction, never an MFA bypass: mailboxes
already enrolled in MFA continue to require MFA even if removed from the list.
"""
from __future__ import annotations

from uuid import UUID

from django.conf import settings


def _canary_ids() -> frozenset[str]:
    raw = getattr(settings, "POSTBOX_2FA_CANARY_MAILBOX_IDS", "")
    result: set[str] = set()
    for item in str(raw or "").split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            result.add(str(UUID(candidate)))
        except (ValueError, AttributeError):
            # Malformed identifiers NEVER expand the allowed set.
            continue
    return frozenset(result)


def can_start_mfa_enrollment(mailbox) -> bool:
    """Select only explicitly allowlisted mailboxes during a scoped canary.

    Canary ON with no valid UUIDs selects NO new mailboxes (fail closed).
    Canary OFF retains the previous global enrollment behavior.
    """
    if not getattr(settings, "POSTBOX_2FA_CANARY_ENABLED", False):
        return True
    return mailbox is not None and str(mailbox.pk) in _canary_ids()


def mailbox_in_mfa_scope(mailbox) -> bool:
    """Keep already-enrolled mailboxes protected after an allowlist removal.

    Never silently allow an enrolled mailbox's app password to log into PostBox
    or let a previously enrolled mailbox lose its management/recovery path.
    """
    if can_start_mfa_enrollment(mailbox):
        return True
    if mailbox is None:
        return False

    from .models import MailboxTwoFactorSetup

    return MailboxTwoFactorSetup.objects.filter(
        mailbox=mailbox, enabled_at__isnull=False,
    ).exists()


def native_primary_required_for_mailbox(mailbox) -> bool:
    """A Required org policy also must never silently fall back to public IMAP."""
    if mailbox_in_mfa_scope(mailbox):
        return True
    if mailbox is None:
        return False

    from apps.tenants.models import OrganizationTwoFactorMode
    from apps.tenants.security_policy import effective_two_factor_policy

    return effective_two_factor_policy(mailbox.tenant) == OrganizationTwoFactorMode.REQUIRE
