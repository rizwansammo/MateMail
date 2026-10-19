"""Organization MFA policy helpers (Phase 7A).

A configured REQUIRE policy never silently becomes effective while the feature
is off. Conversely, an operator cannot enable REQUIRE before all authentication
and recovery surfaces are gated. This module does NOT authenticate a mailbox:
Hub users and PostBox mailbox identities are intentionally independent.
"""
from django.conf import settings

from .models import OrganizationTwoFactorMode


def effective_two_factor_policy(tenant):
    if not getattr(settings, "ORGANIZATION_2FA_POLICY_ENABLED", False):
        return OrganizationTwoFactorMode.OFF
    if (tenant.two_factor_policy == OrganizationTwoFactorMode.REQUIRE
            and not getattr(settings, "ORGANIZATION_2FA_ENFORCEMENT_READY", False)):
        return OrganizationTwoFactorMode.OFF
    return tenant.two_factor_policy


def can_activate_require():
    # Deliberate fail-closed release gate. Require policy cannot be activated
    # until encrypted Hub OTP migration, independent recovery, and Native client
    # session revocation are verified in the *complete* Stage 7A review.
    # Enabling only environment switches must not activate partial security.
    return False
