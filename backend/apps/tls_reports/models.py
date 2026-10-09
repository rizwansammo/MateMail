"""Tenant-isolated, privacy-minimised TLS reporting aggregate telemetry."""
import uuid

from django.db import models


class TlsAggregateReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="tls_reports")
    domain = models.ForeignKey("domains.Domain", on_delete=models.CASCADE, related_name="tls_reports")
    policy_domain = models.CharField(max_length=253, db_index=True)
    reporter = models.CharField(max_length=253)
    report_id = models.CharField(max_length=255)
    policy_type = models.CharField(max_length=32)
    json_sha256 = models.CharField(max_length=64, unique=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    successful_sessions = models.PositiveBigIntegerField(default=0)
    failed_sessions = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "tls_aggregate_report"
        ordering = ["-period_end"]
        indexes = [
            models.Index(fields=["tenant", "domain", "-period_end"], name="tls_tenant_domain_idx"),
        ]


class TlsFailureBucket(models.Model):
    """Only failure category/count; no recipient, IP or free-text details."""
    report = models.ForeignKey(TlsAggregateReport, on_delete=models.CASCADE, related_name="failures")
    result_type = models.CharField(max_length=64)
    count = models.PositiveBigIntegerField()

    class Meta:
        db_table = "tls_failure_bucket"
        constraints = [
            models.UniqueConstraint(fields=["report", "result_type"], name="tls_unique_result_type"),
        ]


class TlsIngestCursor(models.Model):
    address = models.EmailField(unique=True)
    uid_validity = models.PositiveBigIntegerField(default=0)
    last_uid = models.PositiveBigIntegerField(default=0)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    processed_count = models.PositiveBigIntegerField(default=0)
    rejected_count = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "tls_ingest_cursor"
