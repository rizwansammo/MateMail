import hashlib
import secrets
import string

from rest_framework_simplejwt.tokens import RefreshToken


def make_tokens(user, tenant_id=None, *, platform_verified=False, mfa_verified=False):
    """Issue JWTs. Platform OTP proof is set only after successful verification."""
    resolved = str(tenant_id) if tenant_id else _first_tenant_id(user)
    if resolved and not platform_verified:
        # A signup/invitation/secondary token issuer must not bypass Required
        # enrollment. Reject BEFORE writing an outstanding refresh token.
        from apps.tenants.models import AdminHubMfaMode, TenantMembership
        from apps.tenants.admin_hub_mfa_policy import effective_admin_hub_mfa_policy
        membership = TenantMembership.objects.select_related("tenant").filter(
            tenant_id=resolved, user=user, status="active",
        ).first()
        if (effective_admin_hub_mfa_policy(membership) == AdminHubMfaMode.REQUIRED and
                not mfa_verified):
            raise ValueError("Required Hub admin MFA proof is missing")
    if getattr(user, "hub_mfa_reenrollment_required", False) and not mfa_verified:
        raise ValueError("Hub MFA recovery requires new authenticator enrollment")
    refresh = RefreshToken.for_user(user)
    refresh["hub_security_epoch"] = user.hub_security_epoch
    if mfa_verified:
        from django.utils import timezone
        if not getattr(user, "two_factor_enabled", False):
            raise ValueError("MFA proof requires completed 2FA")
        refresh["mfa_verified_at"] = int(timezone.now().timestamp())
    if platform_verified:
        from django.utils import timezone
        if not getattr(user, "is_platform_admin", False):
            raise ValueError("Platform verification requires a platform administrator")
        refresh["platform_otp_verified_at"] = int(timezone.now().timestamp())
    if resolved:
        refresh["tenant_id"] = resolved
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def revoke_all_refresh_tokens(user) -> int:
    """
    Blacklist every outstanding refresh token for `user`, ending all sessions
    that could still be renewed. Called on security events such as a password
    reset.

    Returns the number of tokens newly blacklisted.

    Note: already-issued *access* tokens remain valid until they expire
    (ACCESS_TOKEN_LIFETIME, 15 minutes by default). Revoking those would require
    a deny-list check on every request; the short lifetime bounds the exposure.
    """
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    revoked = 0
    for token in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        if created:
            revoked += 1
    return revoked


def generate_backup_codes(n: int = 10) -> list[str]:
    alphabet = string.ascii_uppercase + string.digits
    return ["".join(secrets.choice(alphabet) for _ in range(8)) for _ in range(n)]


def _first_tenant_id(user) -> str | None:
    from apps.tenants.models import TenantMembership
    mem = (
        TenantMembership.objects
        .filter(user=user, status="active")
        .order_by("created_at")
        .first()
    )
    return str(mem.tenant_id) if mem else None
