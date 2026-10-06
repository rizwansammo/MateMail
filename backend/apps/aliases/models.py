import uuid

from django.core.exceptions import ValidationError
from django.db import models, transaction

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

    def save(self, *args, **kwargs):
        """Reserve Alias addresses in the same namespace as mailbox addresses."""
        normalized = (self.source_address or "").strip().lower()

        if self._state.adding:
            from apps.mail_directory.models import AddressKind
            from apps.mail_directory.services import reserve_address

            with transaction.atomic():
                reserve_address(
                    tenant=self.tenant,
                    domain=self.domain,
                    address=normalized,
                    kind=AddressKind.ALIAS,
                )
                self.source_address = normalized
                return super().save(*args, **kwargs)

        previous = (
            type(self).objects
            .filter(pk=self.pk)
            .values_list("source_address", flat=True)
            .first()
        )
        if previous and previous.lower() != normalized:
            raise ValidationError(
                "Alias addresses cannot be changed in place. Create a new alias instead."
            )
        if previous:
            self.source_address = previous

        return super().save(*args, **kwargs)
