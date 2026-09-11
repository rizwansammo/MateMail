import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class PlanTier(models.TextChoices):
    TRIAL = "trial", "Trial"
    STARTER = "starter", "Starter"
    BUSINESS = "business", "Business"
    INFRASTRUCTURE = "infrastructure", "Infrastructure"


class Plan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.CharField(max_length=30, choices=PlanTier.choices, unique=True)
    display_name = models.CharField(max_length=100)
    price_monthly = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    max_domains = models.PositiveIntegerField()
    max_mailboxes = models.PositiveIntegerField()
    max_members = models.PositiveIntegerField(default=5)

    # ── Storage ──────────────────────────────────────────────────────────────
    #
    # Three separate numbers, because the Mail Engine enforces three separate
    # things and refuses a domain whose values contradict each other:
    #
    #     default_storage_per_mailbox_mb  <=  max_storage_per_mailbox_mb
    #                                     <=  max_storage_total_mb
    #
    # Measured against the live engine, not assumed: a domain created with a
    # per-mailbox ceiling above the domain total is rejected outright
    # (`mailbox_quota_exceeds_domain_quota`), as is a default above the
    # ceiling (`mailbox_defquota_exceeds_mailbox_maxquota`).
    #
    # The total is stored rather than derived. Deriving it — total =
    # mailboxes x ceiling — hard-codes a policy that every mailbox may
    # simultaneously reach its maximum, which forecloses the shared-pool plans
    # this field exists to allow (10 mailboxes, 5 GB each, 25 GB shared). The
    # Mail Engine adapter must never invent this number.
    default_storage_per_mailbox_mb = models.PositiveIntegerField(
        default=1024,
        help_text="Storage a new mailbox starts with, in MB. Must not exceed the per-mailbox maximum.",
    )
    max_storage_per_mailbox_mb = models.PositiveIntegerField(
        help_text="Largest a single mailbox may grow, in MB. Must not exceed the domain total.",
    )
    max_storage_total_mb = models.PositiveIntegerField(
        default=10240,
        help_text=(
            "Total storage shared by every mailbox on a domain, in MB. Set "
            "deliberately — it is a commercial limit, not mailboxes x ceiling."
        ),
    )
    includes_spam_quarantine = models.BooleanField(default=False)
    includes_audit_logs = models.BooleanField(default=False)
    includes_queue_visibility = models.BooleanField(default=False)
    includes_backup_controls = models.BooleanField(default=False)
    includes_team_roles = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "billing_plan"

    def __str__(self):
        return self.display_name


class SubscriptionStatus(models.TextChoices):
    TRIALING = "trialing", "Trialing"
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past Due"
    CANCELLED = "cancelled", "Cancelled"


class Subscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(
        max_length=20, choices=SubscriptionStatus.choices, default=SubscriptionStatus.TRIALING
    )
    # Stripe IDs — reserved for future Stripe integration
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "billing_subscription"

    def __str__(self):
        return f"Subscription [{self.status}]"


class InvoiceStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    VOID = "void", "Void"


class Invoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="invoices"
    )
    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, related_name="invoices"
    )
    invoice_number = models.CharField(max_length=50, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(
        max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.PENDING
    )
    stripe_invoice_id = models.CharField(max_length=255, blank=True)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "billing_invoice"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.invoice_number} ${self.amount} [{self.status}]"
