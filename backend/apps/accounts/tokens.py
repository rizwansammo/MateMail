import hashlib
import secrets
import string
from datetime import timedelta

from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


def make_tokens(user, tenant_id=None):
    """Issue access + refresh JWT pair. Embeds tenant_id into both tokens."""
    refresh = RefreshToken.for_user(user)
    resolved = str(tenant_id) if tenant_id else _first_tenant_id(user)
    if resolved:
        refresh["tenant_id"] = resolved
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def make_partial_token(user, tenant_id):
    """Short-lived access token issued when 2FA is required. Not usable for API calls."""
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=timedelta(minutes=5))
    token["two_fa_required"] = True
    if tenant_id:
        token["tenant_id"] = str(tenant_id)
    return str(token)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


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
