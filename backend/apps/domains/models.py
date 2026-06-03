import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class DomainStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    WARNING = "warning", "Warning"
    FAILED = "failed", "Failed"
    PAUSED = "paused", "Paused"


class Domain(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="domains"
    )
    domain = models.CharField(max_length=255, unique=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=DomainStatus.choices, default=DomainStatus.PENDING
    )
    dns_health_score = models.IntegerField(default=0)

    # DKIM
    dkim_selector = models.CharField(max_length=63, default="mm1")
    # Public key text (for DNS TXT record display). Private key lives in mailcow.
    dkim_public_key = models.TextField(blank=True)
    # PEM private key — provisioned to mail engine in Phase 6; never exposed via API
    dkim_private_key = models.TextField(blank=True)

    # Provisioning state in the mail engine
    mail_engine_provisioned = models.BooleanField(default=False)
    mail_engine_error = models.TextField(blank=True)

    added_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "domains_domain"
        ordering = ["-added_at"]

    def __str__(self):
        return self.domain
