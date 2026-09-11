import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.security.scopes import DEFAULT_SCOPES
from apps.security.scopes import normalise as normalise_scopes


def default_scopes():
    """Callable default so the stored list is never shared between rows."""
    return list(DEFAULT_SCOPES)


class TeamInvite(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="invites")
    email = models.EmailField()
    role = models.CharField(max_length=20, default="admin")
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_invites",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    is_revoked = models.BooleanField(default=False)

    class Meta:
        db_table = "teams_invite"
        ordering = ["-created_at"]

    @property
    def is_pending(self):
        return (
            not self.is_revoked
            and self.accepted_at is None
            and timezone.now() < self.expires_at
        )

    @classmethod
    def make(cls, tenant, email, role, invited_by):
        raw = secrets.token_urlsafe(40)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        obj = cls.objects.create(
            tenant=tenant,
            email=email,
            role=role,
            token_hash=token_hash,
            invited_by=invited_by,
            expires_at=timezone.now() + timedelta(days=7),
        )
        return raw, obj


class APIKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=12)
    key_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_api_keys",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # What this key may change. A key is NOT its creator: it holds only the
    # scopes it was granted, never the permissions of the person who minted it.
    # See apps.security.scopes for the model and the enforcement point.
    scopes = models.JSONField(default=default_scopes)

    class Meta:
        db_table = "teams_api_key"
        ordering = ["-created_at"]

    @classmethod
    def make(cls, tenant, name, created_by, expires_at=None, scopes=None):
        """
        Mint a key. Read-only unless write scopes are asked for explicitly —
        being created by an owner or admin grants a key nothing extra.
        """
        raw = "mm_" + secrets.token_urlsafe(40)
        key_hash = hashlib.sha256(raw.encode()).hexdigest()
        key_prefix = raw[3:11]  # 8 chars shown in UI as mm_{prefix}...
        obj = cls.objects.create(
            tenant=tenant,
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            created_by=created_by,
            expires_at=expires_at,
            scopes=normalise_scopes(scopes if scopes is not None else DEFAULT_SCOPES),
        )
        return raw, obj

    @property
    def is_read_only(self) -> bool:
        return normalise_scopes(self.scopes) == list(DEFAULT_SCOPES)
