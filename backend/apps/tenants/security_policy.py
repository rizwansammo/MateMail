"""Organization MFA policy helpers (Phase 7A).

A configured REQUIRE policy never silently becomes effective while the feature
is off. Conversely, an operator cannot enable REQUIRE before all authentication
and recovery surfaces are gated. This module does NOT authenticate a mailbox:
Hub users and PostBox mailbox identities are intentionally independent.
"""
from django.conf import settings

from .models import OrganizationTwoFactorMode


def effective_two_factor_policy(tenant):
    # Activation flags govern NEW policy mutations, not existing commitments.
    # A previously enforced tenant must never silently become password-only
    # because an operator accidentally disables a rollout flag. Administrators
    # must explicitly change the persisted policy through the authorized path.
    if tenant.two_factor_policy == OrganizationTwoFactorMode.REQUIRE:
        return OrganizationTwoFactorMode.REQUIRE
    if not getattr(settings, "ORGANIZATION_2FA_POLICY_ENABLED", False):
        return OrganizationTwoFactorMode.OFF
    return tenant.two_factor_policy


def can_activate_require():
    """Independent operator-accepted release gate for REQUIRED organization MFA.

    Customer administrators choose their organization's policy, but cannot
    enable Require until both Hub and Native/PostBox are ready for enforcement.
    An accidental partial flag activation must never unlock Require.
    """
    from cryptography.fernet import Fernet

    if not all((
        getattr(settings, "ORGANIZATION_2FA_POLICY_ENABLED", False),
        getattr(settings, "ORGANIZATION_2FA_ENFORCEMENT_READY", False),
        getattr(settings, "ORGANIZATION_2FA_NATIVE_ENFORCEMENT_ACCEPTED", False),
        getattr(settings, "POSTBOX_2FA_ENABLED", False),
        getattr(settings, "SMTP_IMAP_SECURITY_ENABLED", False),
        getattr(settings, "MAIL_ENGINE_ADAPTER", "") == "native",
    )):
        return False
    hub = getattr(settings, "HUB_2FA_FERNET_KEY", "")
    postbox = getattr(settings, "POSTBOX_2FA_FERNET_KEY", "")
    if not hub or not postbox or hub == postbox:
        return False
    try:
        Fernet(hub.encode("ascii") if isinstance(hub, str) else hub)
        Fernet(postbox.encode("ascii") if isinstance(postbox, str) else postbox)
    except (ValueError, TypeError, UnicodeError):
        return False
    return True
