import uuid

from django.core.exceptions import ValidationError
from django.db import models


class AddressKind(models.TextChoices):
    MAILBOX = "mailbox", "Mailbox"
    TEAM_BOX = "team_box", "TeamBox"
    ALIAS = "alias", "Alias"
    FORWARD_GROUP = "forward_group", "Forward Group"


class AddressClaim(models.Model):
    """
    One globally-owned routable email address.

    Mailbox, Alias, TeamBox and Forward Group live in different product tables,
    so none of those tables can enforce cross-type uniqueness by itself.
    Every creation path reserves the normalized address here first; the UNIQUE
    constraint is the race-safe serialization point.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="mail_address_claims",
    )
    domain = models.ForeignKey(
        "domains.Domain",
        on_delete=models.CASCADE,
        related_name="mail_address_claims",
    )
    address = models.EmailField(unique=True, db_index=True)
    kind = models.CharField(max_length=24, choices=AddressKind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mail_directory_address_claim"
        ordering = ["address"]

    def save(self, *args, **kwargs):
        self.address = (self.address or "").strip().lower()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.address} ({self.kind})"


class AccessGrantKind(models.TextChoices):
    TEAM_BOX = "team_box", "TeamBox membership"
    DELEGATION = "delegation", "Mailbox delegation"


class MailboxAccessGrant(models.Model):
    """
    Permission for one authenticated personal mailbox to act on another mailbox.

    The grantee is a Mailbox rather than a Workspace User because PostBox's
    security identity is deliberately mailbox-bound. A Workspace admin login
    alone must never imply permission to read mail.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="mailbox_access_grants",
    )
    target_mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.CASCADE,
        related_name="access_grants_received",
    )
    grantee_mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.CASCADE,
        related_name="access_grants",
    )
    grant_type = models.CharField(max_length=20, choices=AccessGrantKind.choices)
    can_read = models.BooleanField(default=True)
    can_manage = models.BooleanField(default=False)
    can_send_as = models.BooleanField(default=False)
    can_send_on_behalf = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mail_directory_access_grant"
        ordering = ["target_mailbox", "grantee_mailbox"]
        constraints = [
            models.UniqueConstraint(
                fields=["target_mailbox", "grantee_mailbox"],
                name="uniq_mailbox_access_pair",
            ),
        ]
        indexes = [
            models.Index(
                fields=["grantee_mailbox", "active"],
                name="mail_access_grantee_idx",
            ),
            models.Index(
                fields=["target_mailbox", "active"],
                name="mail_access_target_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}

        if self.target_mailbox_id and self.grantee_mailbox_id:
            if self.target_mailbox_id == self.grantee_mailbox_id:
                errors["grantee_mailbox"] = "A mailbox does not need a grant to access itself."

            if self.tenant_id:
                if self.target_mailbox.tenant_id != self.tenant_id:
                    errors["target_mailbox"] = "Target mailbox is not in this organization."
                if self.grantee_mailbox.tenant_id != self.tenant_id:
                    errors["grantee_mailbox"] = "Grantee mailbox is not in this organization."

            from apps.mailboxes.models import MailboxKind

            if self.grantee_mailbox.kind != MailboxKind.PERSONAL:
                errors["grantee_mailbox"] = (
                    "Only a personal mailbox can receive mailbox access."
                )

            if (
                self.grant_type == AccessGrantKind.TEAM_BOX
                and self.target_mailbox.kind != MailboxKind.TEAM_BOX
            ):
                errors["target_mailbox"] = "TeamBox membership requires a TeamBox target."

            if (
                self.grant_type == AccessGrantKind.DELEGATION
                and self.target_mailbox.kind != MailboxKind.PERSONAL
            ):
                errors["target_mailbox"] = (
                    "Delegation requires a personal mailbox target."
                )

        if self.can_manage and not self.can_read:
            errors["can_manage"] = "Manage permission requires read permission."

        if self.active and not any(
            (self.can_read, self.can_manage, self.can_send_as, self.can_send_on_behalf)
        ):
            errors["active"] = "An active access grant must grant at least one permission."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.grantee_mailbox_id} -> {self.target_mailbox_id} "
            f"({self.grant_type})"
        )
