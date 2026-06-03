import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class MailboxStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"
    SUSPENDED = "suspended", "Suspended"


class Mailbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="mailboxes"
    )
    domain = models.ForeignKey(
        "domains.Domain", on_delete=models.CASCADE, related_name="mailboxes"
    )
    full_name = models.CharField(max_length=255)
    local_part = models.CharField(max_length=64, db_index=True)
    # Derived field kept in sync: local_part@domain.domain
    email = models.EmailField(unique=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=MailboxStatus.choices, default=MailboxStatus.ACTIVE
    )
    quota_mb = models.PositiveIntegerField(default=10240)       # 10 GB
    storage_used_mb = models.PositiveIntegerField(default=0)

    # Mail engine provisioning
    mail_engine_provisioned = models.BooleanField(default=False)
    mail_engine_error = models.TextField(blank=True)

    last_login = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "mailboxes_mailbox"
        ordering = ["email"]
        unique_together = [("tenant", "local_part", "domain")]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = f"{self.local_part}@{self.domain.domain}"
        super().save(*args, **kwargs)
