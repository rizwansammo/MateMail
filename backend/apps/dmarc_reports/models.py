"""Minimal, tenant-bound DMARC telemetry. Never persist raw RFC822 or XML."""
import uuid

from django.db import models


class AggregateReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Platform's own mail.matemail.pro has no customer Domain row, so it is
    # tracked for operators alone with tenant=NULL / domain=NULL.
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="dmarc_reports",
        null=True, blank=True,
    )
    domain = models.ForeignKey(
        "domains.Domain", on_delete=models.CASCADE, related_name="dmarc_reports",
        null=True, blank=True,
    )
    policy_domain = models.CharField(max_length=253, db_index=True)
    reporter = models.CharField(max_length=253)
    report_identifier = models.CharField(max_length=255)
    # The digest is content-based: an identical report sent twice, or in a
    # differently compressed file, must be ingested at most once.
    xml_sha256 = models.CharField(max_length=64, unique=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    message_count = models.PositiveBigIntegerField(default=0)
    spf_pass = models.PositiveBigIntegerField(default=0)
    dkim_pass = models.PositiveBigIntegerField(default=0)
    dmarc_pass = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "dmarc_aggregate_report"
        indexes = [
            models.Index(fields=["tenant", "policy_domain", "-period_end"],
                         name="dmarc_tenant_period_idx"),
        ]
        ordering = ["-period_end"]

    def __str__(self):
        return f"DMARC {self.policy_domain} / {self.reporter}"


class AggregateRecord(models.Model):
    """Aggregate rows, not email addresses or individual messages."""
    report = models.ForeignKey(
        AggregateReport, on_delete=models.CASCADE, related_name="records"
    )
    source_ip = models.GenericIPAddressField()
    count = models.PositiveBigIntegerField()
    disposition = models.CharField(max_length=16)  # none/quarantine/reject
    spf_result = models.CharField(max_length=32)
    dkim_result = models.CharField(max_length=32)

    class Meta:
        db_table = "dmarc_aggregate_record"
        indexes = [
            models.Index(fields=["source_ip"], name="dmarc_source_ip_idx"),
        ]


class IngestCursor(models.Model):
    """One read-only IMAP folder cursor. Reset safely on UIDVALIDITY changes."""
    address = models.EmailField(unique=True)
    uid_validity = models.PositiveBigIntegerField(default=0)
    last_uid = models.PositiveBigIntegerField(default=0)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "dmarc_ingest_cursor"
