import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class LogEventType(models.TextChoices):
    MAILBOX_CREATED = "mailbox_created", "Mailbox Created"
    MAILBOX_DELETED = "mailbox_deleted", "Mailbox Deleted"
    MAILBOX_DISABLED = "mailbox_disabled", "Mailbox Disabled"
    DOMAIN_ADDED = "domain_added", "Domain Added"
    DOMAIN_DELETED = "domain_deleted", "Domain Deleted"
    TENANT_SUSPENDED = "tenant_suspended", "Tenant Suspended"
    TENANT_REACTIVATED = "tenant_reactivated", "Tenant Reactivated"
    PLAN_CHANGED = "plan_changed", "Plan Changed"
    LOGIN = "login", "Login"
    LOGOUT = "logout", "Logout"


class MailLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="mail_logs",
    )
    event_type = models.CharField(max_length=64)
    source = models.CharField(max_length=255, blank=True, default="")
    result = models.CharField(max_length=64, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "event_type"]),
        ]

    def __str__(self):
        return f"{self.event_type} [{self.tenant_id}] @ {self.created_at}"
