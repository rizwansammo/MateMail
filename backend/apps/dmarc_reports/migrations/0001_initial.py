# P4-B: DMARC aggregate report telemetry. Raw messages are never stored.
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("tenants", "0001_initial"),
        ("domains", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="AggregateReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("policy_domain", models.CharField(db_index=True, max_length=253)),
                ("reporter", models.CharField(max_length=253)),
                ("report_identifier", models.CharField(max_length=255)),
                ("xml_sha256", models.CharField(max_length=64, unique=True)),
                ("period_start", models.DateTimeField()),
                ("period_end", models.DateTimeField()),
                ("message_count", models.PositiveBigIntegerField(default=0)),
                ("spf_pass", models.PositiveBigIntegerField(default=0)),
                ("dkim_pass", models.PositiveBigIntegerField(default=0)),
                ("dmarc_pass", models.PositiveBigIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("domain", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="dmarc_reports", to="domains.domain")),
                ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="dmarc_reports", to="tenants.tenant")),
            ],
            options={"db_table": "dmarc_aggregate_report", "ordering": ["-period_end"]},
        ),
        migrations.CreateModel(
            name="AggregateRecord",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("source_ip", models.GenericIPAddressField()),
                ("count", models.PositiveBigIntegerField()),
                ("disposition", models.CharField(max_length=16)),
                ("spf_result", models.CharField(max_length=32)),
                ("dkim_result", models.CharField(max_length=32)),
                ("report", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="records", to="dmarc_reports.aggregatereport")),
            ],
            options={"db_table": "dmarc_aggregate_record"},
        ),
        migrations.CreateModel(
            name="IngestCursor",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("address", models.EmailField(max_length=254, unique=True)),
                ("uid_validity", models.PositiveBigIntegerField(default=0)),
                ("last_uid", models.PositiveBigIntegerField(default=0)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=200)),
            ],
            options={"db_table": "dmarc_ingest_cursor"},
        ),
        migrations.AddIndex(
            model_name="aggregatereport",
            index=models.Index(fields=["tenant", "policy_domain", "-period_end"], name="dmarc_tenant_period_idx"),
        ),
        migrations.AddIndex(
            model_name="aggregaterecord",
            index=models.Index(fields=["source_ip"], name="dmarc_source_ip_idx"),
        ),
    ]
