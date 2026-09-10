import hashlib
import secrets
import string

from rest_framework_simplejwt.tokens import RefreshToken


def make_tokens(user, tenant_id=None):
    """Issue access + refresh JWT pair. Embeds tenant_id into both tokens."""
    refresh = RefreshToken.for_user(user)
    resolved = str(tenant_id) if tenant_id else _first_tenant_id(user)
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
