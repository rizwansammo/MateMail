import uuid

from django.core.exceptions import ValidationError
from django.db import models, transaction

from apps.tenants.managers import TenantScopedManager


class ForwardGroupStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"


class ForwardGroupSenderPolicy(models.TextChoices):
    ANYONE = "anyone", "Anyone"
    ORGANIZATION = "organization", "Organization only"
    MEMBERS = "members", "Members only"
    SELECTED = "selected", "Selected senders"


class ForwardGroupMemberRole(models.TextChoices):
    MEMBER = "member", "Member"
    OWNER = "owner", "Owner"


class ForwardGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="forward_groups"
    )
    domain = models.ForeignKey(
        "domains.Domain", on_delete=models.CASCADE, related_name="forward_groups"
    )
    local_part = models.CharField(max_length=64, db_index=True)
    address = models.EmailField(unique=True, db_index=True)
    display_name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=ForwardGroupStatus.choices,
        default=ForwardGroupStatus.ACTIVE,
    )
    sender_policy = models.CharField(
        max_length=20,
        choices=ForwardGroupSenderPolicy.choices,
        default=ForwardGroupSenderPolicy.ANYONE,
    )
    mail_engine_provisioned = models.BooleanField(default=False)
    mail_engine_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "forward_groups_forward_group"
        ordering = ["address"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "domain", "local_part"],
                name="uniq_forward_group_local_part",
            )
        ]

    def __str__(self):
        return self.address

    def clean(self):
        super().clean()
        if self.domain_id and self.tenant_id and self.domain.tenant_id != self.tenant_id:
            raise ValidationError({"domain": "Domain is not in this organization."})

    def save(self, *args, **kwargs):
        local_part = (self.local_part or "").strip().lower()
        address = f"{local_part}@{self.domain.domain}".lower()

        if self._state.adding:
            from apps.mail_directory.models import AddressKind
            from apps.mail_directory.services import reserve_address

            self.clean()
            with transaction.atomic():
                reserve_address(
                    tenant=self.tenant,
                    domain=self.domain,
                    address=address,
                    kind=AddressKind.FORWARD_GROUP,
                )
                self.local_part = local_part
                self.address = address
                return super().save(*args, **kwargs)

        previous = (
            type(self).objects
            .filter(pk=self.pk)
            .values("address", "local_part", "domain_id")
            .first()
        )
        if previous:
            if (
                previous["address"].lower() != address
                or previous["domain_id"] != self.domain_id
            ):
                raise ValidationError(
                    "Forward Group addresses cannot be changed in place."
                )
            self.address = previous["address"]
            self.local_part = previous["local_part"]

        self.clean()
        return super().save(*args, **kwargs)


class ForwardGroupMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        ForwardGroup, on_delete=models.CASCADE, related_name="members"
    )
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.CASCADE,
        related_name="forward_group_memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=ForwardGroupMemberRole.choices,
        default=ForwardGroupMemberRole.MEMBER,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "forward_groups_member"
        ordering = ["mailbox__email"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "mailbox"],
                name="uniq_forward_group_member",
            )
        ]

    def clean(self):
        super().clean()
        if self.group_id and self.mailbox_id:
            if self.group.tenant_id != self.mailbox.tenant_id:
                raise ValidationError(
                    {"mailbox": "Mailbox is not in this organization."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ForwardGroupAllowedSender(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        ForwardGroup, on_delete=models.CASCADE, related_name="allowed_senders"
    )
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.CASCADE,
        related_name="forward_group_sender_grants",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "forward_groups_allowed_sender"
        ordering = ["mailbox__email"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "mailbox"],
                name="uniq_forward_group_allowed_sender",
            )
        ]

    def clean(self):
        super().clean()
        from apps.mailboxes.models import MailboxKind

        if self.group_id and self.mailbox_id:
            if self.group.tenant_id != self.mailbox.tenant_id:
                raise ValidationError(
                    {"mailbox": "Mailbox is not in this organization."}
                )
            if self.mailbox.kind != MailboxKind.PERSONAL:
                raise ValidationError(
                    {"mailbox": "Only a personal mailbox can be an allowed sender."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
