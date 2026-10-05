import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q


class TenantStatus(models.TextChoices):
    """
    A workspace's lifecycle state.

    `PENDING_APPROVAL` is the state every new workspace starts in. During the
    Private Beta MateMail is free and admin-approved (DEC-016), and approval is
    a real gate rather than a policy someone remembers: a workspace in this
    state exists, can be signed into and configured, and can provision nothing
    into the Mail Engine.

    It is a distinct status rather than a reuse of TRIAL because the two answer
    different questions. TRIAL means "paying nothing yet"; PENDING_APPROVAL
    means "not yet allowed to send mail". Collapsing them would make every
    `status == TRIAL` check ambiguous about which it meant.
    """

    PENDING_APPROVAL = "pending_approval", "Pending Approval"
    REJECTED = "rejected", "Rejected"
    TRIAL = "trial", "Trial"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past Due"
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


#: The only states in which a workspace may create or change Mail Engine
#: resources. Everything else — pending, rejected, suspended, cancelled — is
#: denied. Listed as an allowlist on purpose: a status added later is denied
#: until someone decides otherwise, rather than silently permitted.
MAIL_ENABLED_STATUSES = frozenset({
    TenantStatus.TRIAL,
    TenantStatus.ACTIVE,
})


class TenantManager(models.Manager):
    pass


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    # Longer than the old 20 to fit "pending_approval" (16) with headroom.
    status = models.CharField(
        max_length=32,
        choices=TenantStatus.choices,
        default=TenantStatus.PENDING_APPROVAL,
    )
    plan = models.CharField(max_length=50, default="starter")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_tenants",
    )
    # ── Approval (P5) ────────────────────────────────────────────────────────
    # Who let this workspace send mail, when, and — if refused — why. Kept on
    # the tenant as well as in the audit log: the audit log is the history, and
    # these are the current facts a policy check reads without a join.
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="approved_tenants",
    )
    #: Customer-safe. Shown to the workspace owner, so it must not contain
    #: internal notes — the audit log is where those belong.
    review_reason = models.TextField(blank=True)

    #: Set when a platform admin disables outbound mail without suspending the
    #: whole workspace. The workspace keeps working, receives mail, and can be
    #: administered; it simply cannot send. The lighter of the two abuse
    #: responses, and reversible.
    outbound_disabled = models.BooleanField(default=False)
    outbound_disabled_at = models.DateTimeField(null=True, blank=True)

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
    def is_approved(self) -> bool:
        """A platform admin has explicitly let this workspace use the engine."""
        return self.status in MAIL_ENABLED_STATUSES and self.approved_at is not None

    @property
    def can_use_mail(self) -> bool:
        """
        May this workspace create or change Mail Engine resources?

        The single authoritative answer. Every provisioning path asks this and
        nothing re-implements the rule — scattered `status !=` checks are how
        one endpoint ends up enforcing a policy the next one forgot.

        Fails closed: a status nobody has thought about yet is not mail-enabled,
        and an unapproved workspace is not mail-enabled however it got its
        status.
        """
        return self.is_approved

    @property
    def can_send_mail(self) -> bool:
        """
        May this workspace send outbound mail right now?

        Narrower than `can_use_mail`: outbound can be switched off on its own as
        an abuse response, leaving the workspace otherwise intact. Consulted by
        the SMTP policy bridge on every submission.
        """
        return self.can_use_mail and not self.outbound_disabled


class CustomHostnameSurface(models.TextChoices):
    """Which customer-facing MateMail surface a hostname serves."""

    HUB = "hub", "MateMail Hub"
    POSTBOX = "postbox", "PostBox"


class CustomHostnameDNSStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VERIFIED = "verified", "Verified"
    FAILED = "failed", "Failed"


class CustomHostnameProvisioningStatus(models.TextChoices):
    """
    Edge lifecycle.

    READY means nginx/TLS provisioning completed but application routing has
    not been activated yet. Keeping READY separate from ACTIVE lets Phase 3
    issue a certificate safely before Phase 4 starts accepting tenant traffic.
    """

    UNPROVISIONED = "unprovisioned", "Unprovisioned"
    PROVISIONING = "provisioning", "Provisioning"
    READY = "ready", "Ready"
    ACTIVE = "active", "Active"
    ERROR = "error", "Error"
    DEACTIVATING = "deactivating", "Deactivating"
    INACTIVE = "inactive", "Inactive"


class CustomHostnameCertificateStatus(models.TextChoices):
    NOT_REQUESTED = "not_requested", "Not requested"
    ISSUING = "issuing", "Issuing"
    ACTIVE = "active", "Active"
    ERROR = "error", "Error"
    REVOKED = "revoked", "Revoked"


#: Every state except INACTIVE owns the hostname and the tenant/surface slot.
#: The database constraints below use an explicit allow-list so a new state is
#: denied until somebody decides whether it should own those resources.
CUSTOM_HOSTNAME_LIVE_STATES = (
    CustomHostnameProvisioningStatus.UNPROVISIONED,
    CustomHostnameProvisioningStatus.PROVISIONING,
    CustomHostnameProvisioningStatus.READY,
    CustomHostnameProvisioningStatus.ACTIVE,
    CustomHostnameProvisioningStatus.ERROR,
    CustomHostnameProvisioningStatus.DEACTIVATING,
)


class CustomHostname(models.Model):
    """
    A customer-owned HTTPS hostname bound to one MateMail organization surface.

    This is web-access state only. It does not represent a mail domain and does
    not participate in MX/SPF/DKIM/DMARC or Mail Engine provisioning.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="custom_hostnames",
    )
    hostname = models.CharField(max_length=253)
    surface = models.CharField(max_length=16, choices=CustomHostnameSurface.choices)

    dns_status = models.CharField(
        max_length=16,
        choices=CustomHostnameDNSStatus.choices,
        default=CustomHostnameDNSStatus.PENDING,
    )
    dns_verified_at = models.DateTimeField(null=True, blank=True)
    dns_last_checked_at = models.DateTimeField(null=True, blank=True)

    provisioning_status = models.CharField(
        max_length=20,
        choices=CustomHostnameProvisioningStatus.choices,
        default=CustomHostnameProvisioningStatus.UNPROVISIONED,
    )
    certificate_status = models.CharField(
        max_length=20,
        choices=CustomHostnameCertificateStatus.choices,
        default=CustomHostnameCertificateStatus.NOT_REQUESTED,
    )

    #: Customer-safe summary of the most recent DNS/provisioning failure.
    #: Raw resolver output and command stderr never belong here.
    last_error = models.TextField(blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenants_custom_hostname"
        ordering = ["surface", "created_at"]
        constraints = [
            # A live hostname can belong to exactly one organization/surface.
            # INACTIVE history does not reserve the name forever.
            models.UniqueConstraint(
                fields=["hostname"],
                condition=Q(provisioning_status__in=CUSTOM_HOSTNAME_LIVE_STATES),
                name="uniq_live_custom_hostname",
            ),
            # First release: one live Hub hostname and one live PostBox hostname
            # per organization. Old inactive rows are retained for audit/history.
            models.UniqueConstraint(
                fields=["tenant", "surface"],
                condition=Q(provisioning_status__in=CUSTOM_HOSTNAME_LIVE_STATES),
                name="uniq_live_custom_host_surface",
            ),
        ]

    def __str__(self):
        return f"{self.hostname} → {self.tenant.slug}/{self.surface}"

    @property
    def is_dns_verified(self) -> bool:
        return self.dns_status == CustomHostnameDNSStatus.VERIFIED

    @property
    def is_request_active(self) -> bool:
        return self.provisioning_status == CustomHostnameProvisioningStatus.ACTIVE


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
