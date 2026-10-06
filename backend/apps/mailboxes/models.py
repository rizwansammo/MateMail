import uuid

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.tenants.managers import TenantScopedManager


class MailboxStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"
    SUSPENDED = "suspended", "Suspended"


class MailboxKind(models.TextChoices):
    PERSONAL = "personal", "Personal mailbox"
    TEAM_BOX = "team_box", "TeamBox"


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
    kind = models.CharField(
        max_length=20,
        choices=MailboxKind.choices,
        default=MailboxKind.PERSONAL,
        db_index=True,
    )
    status = models.CharField(
        max_length=20, choices=MailboxStatus.choices, default=MailboxStatus.ACTIVE
    )
    quota_mb = models.PositiveIntegerField(default=10240)
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
        """
        Reserve new mailbox addresses in the global MateMail address namespace.

        Mailbox address and kind are intentionally immutable after creation.
        Renaming or converting a live mailbox changes delivery/authentication
        semantics and must be an explicit future workflow.
        """
        candidate_local = (self.local_part or "").strip().lower()
        candidate_email = f"{candidate_local}@{self.domain.domain}".lower()

        if self._state.adding:
            from apps.mail_directory.models import AddressKind
            from apps.mail_directory.services import reserve_address

            claim_kind = (
                AddressKind.TEAM_BOX
                if self.kind == MailboxKind.TEAM_BOX
                else AddressKind.MAILBOX
            )
            with transaction.atomic():
                reserve_address(
                    tenant=self.tenant,
                    domain=self.domain,
                    address=candidate_email,
                    kind=claim_kind,
                )
                self.local_part = candidate_local
                self.email = candidate_email
                return super().save(*args, **kwargs)

        previous = (
            type(self).objects
            .filter(pk=self.pk)
            .values("email", "kind")
            .first()
        )
        if previous:
            if previous["email"].lower() != candidate_email:
                raise ValidationError(
                    "Mailbox addresses cannot be changed in place. "
                    "Create a new mailbox instead."
                )
            if previous["kind"] != self.kind:
                raise ValidationError("Mailbox type cannot be changed in place.")
            self.email = previous["email"]

        return super().save(*args, **kwargs)
