import uuid
from django.conf import settings
from django.db import models


class TenantStatus(models.TextChoices):
    TRIAL = "trial", "Trial"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past Due"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class TenantManager(models.Manager):
    pass


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    status = models.CharField(max_length=20, choices=TenantStatus.choices, default=TenantStatus.TRIAL)
    plan = models.CharField(max_length=50, default="starter")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_tenants",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantManager()

    class Meta:
        db_table = "tenants_tenant"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.status})"

    @property
    def is_active(self):
        return self.status in (TenantStatus.TRIAL, TenantStatus.ACTIVE)

    @property
    def can_send_mail(self):
        return self.status not in (TenantStatus.SUSPENDED, TenantStatus.CANCELLED)


class MemberRole(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Admin"
    SUPPORT = "support", "Support"
    READ_ONLY = "read_only", "Read-only"


class MemberStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    INVITED = "invited", "Invited"
    REMOVED = "removed", "Removed"


class TenantMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=20, choices=MemberRole.choices, default=MemberRole.READ_ONLY)
    status = models.CharField(max_length=20, choices=MemberStatus.choices, default=MemberStatus.INVITED)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="sent_invitations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenants_membership"
        unique_together = [("tenant", "user")]
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user.email} → {self.tenant.name} ({self.role})"
