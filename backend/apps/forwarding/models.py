import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class ForwardingStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    DISABLED = "disabled", "Disabled"


class ForwardingRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="forwarding_rules"
    )
    source_mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.CASCADE,
        related_name="forwarding_rules",
    )
    destination_email = models.EmailField()
    keep_copy = models.BooleanField(default=True)
    status = models.CharField(
        max_length=20, choices=ForwardingStatus.choices, default=ForwardingStatus.ACTIVE
    )
    mail_engine_provisioned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "forwarding_rule"
        ordering = ["source_mailbox"]
        unique_together = [("source_mailbox", "destination_email")]

    def __str__(self):
        return str(self.destination_email)
