import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


PERMISSION_LABELS = {
    "mailbox.read": "See this mailbox's address and basic details",
    "mail.send": "Send email from this mailbox",
    "mail.read": "Read email in this mailbox",
    "mail.modify": "Mark email as read or unread",
    "signatures.read": "Use this mailbox's signatures",
}
ALLOWED_PERMISSIONS = frozenset(PERMISSION_LABELS)

PURPOSE_LABELS = {
    "sales_crm": "Sales / CRM",
    "helpdesk": "Helpdesk / Ticketing",
    "custom": "Custom application",
}
PURPOSE_PERMISSIONS = {
    "sales_crm": ["mailbox.read", "mail.send", "signatures.read"],
    "helpdesk": ["mailbox.read", "mail.read", "mail.modify", "mail.send"],
    "custom": ["mailbox.read"],
}


def default_permissions():
    return list(PURPOSE_PERMISSIONS["sales_crm"])


def normalise_permissions(values):
    if not isinstance(values, (list, tuple, set)):
        values = []
    return sorted({str(value) for value in values if str(value) in ALLOWED_PERMISSIONS})


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class Integration(models.Model):
    """One connected application bound to one exact tenant mailbox."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="integrations"
    )
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.PROTECT, related_name="integrations"
    )
    name = models.CharField(max_length=100)
    purpose = models.CharField(max_length=20, default="custom")
    secret_prefix = models.CharField(max_length=16)
    secret_hash = models.CharField(max_length=64, unique=True)
    permissions = models.JSONField(default=default_permissions)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_integrations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "integrations_integration"
        ordering = ["-created_at"]

    @property
    def is_active(self):
        return self.revoked_at is None

    @classmethod
    def issue(cls, *, tenant, mailbox, name, purpose="custom", created_by, permissions):
        raw = "mmi_" + secrets.token_urlsafe(40)
        obj = cls.objects.create(
            tenant=tenant,
            mailbox=mailbox,
            name=name,
            purpose=purpose if purpose in PURPOSE_LABELS else "custom",
            secret_prefix=raw[:12],
            secret_hash=_digest(raw),
            created_by=created_by,
            permissions=normalise_permissions(permissions),
        )
        return raw, obj

    def has_permission(self, permission: str) -> bool:
        return permission in normalise_permissions(self.permissions)


class ConnectionRequest(models.Model):
    """Short-lived approval request opened in the MateMail Portal."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    integration = models.ForeignKey(
        Integration, on_delete=models.CASCADE, related_name="connection_requests"
    )
    request_hash = models.CharField(max_length=64, unique=True)
    poll_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="approved_integration_connections",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "integrations_connection_request"

    @classmethod
    def issue(cls, integration):
        raw_request = "mmr_" + secrets.token_urlsafe(32)
        raw_poll = "mmp_" + secrets.token_urlsafe(32)
        obj = cls.objects.create(
            integration=integration,
            request_hash=_digest(raw_request),
            poll_hash=_digest(raw_poll),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        return raw_request, raw_poll, obj

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class AccessToken(models.Model):
    """Revocable operational credential issued only after Portal approval."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    integration = models.ForeignKey(
        Integration, on_delete=models.CASCADE, related_name="access_tokens"
    )
    token_prefix = models.CharField(max_length=16)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "integrations_access_token"

    @classmethod
    def issue(cls, integration):
        raw = "mmc_" + secrets.token_urlsafe(48)
        obj = cls.objects.create(
            integration=integration,
            token_prefix=raw[:12],
            token_hash=_digest(raw),
        )
        return raw, obj

    @property
    def is_active(self):
        return self.revoked_at is None

    @classmethod
    def for_raw(cls, raw):
        if not raw:
            return None
        return (
            cls.objects.select_related("integration__tenant", "integration__mailbox")
            .filter(token_hash=_digest(raw), revoked_at__isnull=True)
            .first()
        )



class IntegrationDelivery(models.Model):
    integration = models.ForeignKey(
        Integration, on_delete=models.CASCADE, related_name="deliveries"
    )
    idempotency_key = models.CharField(max_length=100)
    status = models.CharField(max_length=16, default="processing")
    message_id = models.CharField(max_length=998, blank=True, default="")
    filed_in_sent = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "integrations_delivery"
        constraints = [
            models.UniqueConstraint(
                fields=["integration", "idempotency_key"],
                name="uniq_integration_delivery_key",
            )
        ]
