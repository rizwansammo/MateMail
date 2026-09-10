"""
Shared test helpers.

No secrets or production values live here — every credential is generated
per-test and is meaningless outside the test database.
"""
from unittest import mock

import pyotp
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from apps.accounts.models import TwoFactorSetup, User
from apps.domains.models import Domain, DomainStatus
from apps.mailboxes.models import Mailbox
from apps.tenants.models import MemberRole, MemberStatus, Tenant, TenantMembership

# Test-only: the production PBKDF2 hasher costs ~0.3s per call, which dominates
# suite runtime when every test creates several users. This is applied via
# override_settings in test classes only and never affects any real setting.
FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Meets the project's 10-char minimum and the default validators.
TEST_PASSWORD = "Test-Passphrase-42"


def make_user(email, *, verified=True, active=True, password=TEST_PASSWORD, **extra):
    user = User.objects.create_user(email=email, password=password, **extra)
    user.email_verified = verified
    user.is_active = active
    user.save(update_fields=["email_verified", "is_active"])
    return user


def make_tenant(owner, name="Acme", slug="acme", status="active"):
    tenant = Tenant.objects.create(name=name, slug=slug, owner=owner, status=status)
    TenantMembership.objects.create(
        tenant=tenant, user=owner, role=MemberRole.OWNER, status=MemberStatus.ACTIVE
    )
    return tenant


def add_member(tenant, user, role):
    return TenantMembership.objects.create(
        tenant=tenant, user=user, role=role, status=MemberStatus.ACTIVE
    )


def make_domain(tenant, domain="acme-test.example", status=DomainStatus.ACTIVE):
    return Domain.objects.create(tenant=tenant, domain=domain, status=status)


def make_mailbox(tenant, domain, local_part="alice", full_name="Alice"):
    return Mailbox.objects.create(
        tenant=tenant, domain=domain, local_part=local_part, full_name=full_name
    )


def enable_2fa(user):
    """Turn on 2FA for `user` and return the TOTP secret."""
    secret = pyotp.random_base32()
    TwoFactorSetup.objects.create(user=user, totp_secret=secret)
    user.two_factor_enabled = True
    user.save(update_fields=["two_factor_enabled"])
    return secret


def totp_now(secret):
    return pyotp.TOTP(secret).now()


def auth_client(user, tenant=None):
    """An APIClient carrying a real access token for `user` (2FA not required)."""
    from apps.accounts.tokens import make_tokens

    tokens = make_tokens(user, tenant_id=tenant.id if tenant else None)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return client


def bearer_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_token}")
    return client


def disable_throttling(testcase):
    """
    Switch off DRF rate limiting for the duration of `testcase`.

    DRF reads DEFAULT_THROTTLE_RATES into SimpleRateThrottle.THROTTLE_RATES at
    import time, so override_settings(REST_FRAMEWORK=...) does not reach it —
    the dict has to be patched directly. A rate of None makes allow_request()
    return True immediately.

    Use this only in tests asserting authorization outcomes, so that per-IP
    throttling cannot turn a later request into a 429 and mask the real result.
    """
    patcher = mock.patch.dict(
        SimpleRateThrottle.THROTTLE_RATES,
        {"anon": None, "user": None, "auth": None},
    )
    patcher.start()
    testcase.addCleanup(patcher.stop)
