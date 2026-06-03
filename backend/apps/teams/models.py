import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


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

    class Meta:
        db_table = "teams_api_key"
        ordering = ["-created_at"]

    @classmethod
    def make(cls, tenant, name, created_by, expires_at=None):
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
        )
        return raw, obj
