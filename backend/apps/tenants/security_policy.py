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
    if tenant.two_factor_policy == OrganizationTwoFactorMode.REQUIRE:
        # Stage 7A foundation has no runtime enforcement yet. Even a manually
        # seeded value or mis-set environment flag must not claim enforcement.
        return OrganizationTwoFactorMode.OFF
    return tenant.two_factor_policy


def can_activate_require():
    # Deliberate fail-closed stub: enabling feature flags must NEVER expose an
    # unenforced Require policy. Stage 7A's later auth integration must replace
    # this only after Hub JWT, invitations, PostBox and recovery are all gated.
    return False
