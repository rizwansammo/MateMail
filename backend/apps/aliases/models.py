import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class AliasStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"


class Alias(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="aliases"
    )
    domain = models.ForeignKey(
        "domains.Domain", on_delete=models.CASCADE, related_name="aliases"
    )
    source_address = models.EmailField(unique=True, db_index=True)
    destination_mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="incoming_aliases",
    )
    destination_address = models.EmailField(blank=True)
    status = models.CharField(
        max_length=20, choices=AliasStatus.choices, default=AliasStatus.ACTIVE
    )
    mail_engine_provisioned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "aliases_alias"
        ordering = ["source_address"]

    def __str__(self):
        return str(self.source_address)
