import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class DNSCheckStatus(models.TextChoices):
    VERIFIED = "verified", "Verified"
    PENDING = "pending", "Pending"
    MISSING = "missing", "Missing"
    FAILED = "failed", "Failed"


class DNSRecordCheck(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="dns_checks"
    )
    domain = models.ForeignKey(
        "domains.Domain", on_delete=models.CASCADE, related_name="dns_checks"
    )
    record_type = models.CharField(max_length=10)   # MX, TXT, CNAME
    host = models.CharField(max_length=255)
    expected_value = models.TextField()
    detected_value = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=DNSCheckStatus.choices, default=DNSCheckStatus.PENDING
    )
    last_checked = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "dnshealth_record_check"
        ordering = ["domain", "record_type"]
        unique_together = [("domain", "record_type", "host")]

    def __str__(self):
        return f"{self.record_type} {self.host} ({self.status})"
