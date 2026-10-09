# P4-C.E — additive TLS-RPT telemetry tables (no mail engine modifications).
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [("tenants", "0001_initial"), ("domains", "0001_initial")]
    operations = [
        migrations.CreateModel(
            name="TlsAggregateReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("policy_domain", models.CharField(db_index=True, max_length=253)),
                ("reporter", models.CharField(max_length=253)),
                ("report_id", models.CharField(max_length=255)),
                ("policy_type", models.CharField(max_length=32)),
                ("json_sha256", models.CharField(max_length=64, unique=True)),
                ("period_start", models.DateTimeField()),
                ("period_end", models.DateTimeField()),
                ("successful_sessions", models.PositiveBigIntegerField(default=0)),
                ("failed_sessions", models.PositiveBigIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tls_reports", to="tenants.tenant")),
                ("domain", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tls_reports", to="domains.domain")),
            ],
            options={"db_table": "tls_aggregate_report", "ordering": ["-period_end"]},
        ),
        migrations.CreateModel(
            name="TlsFailureBucket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("result_type", models.CharField(max_length=64)),
                ("count", models.PositiveBigIntegerField()),
                ("report", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="failures", to="tls_reports.tlsaggregatereport")),
            ],
            options={"db_table": "tls_failure_bucket"},
        ),
        migrations.CreateModel(
            name="TlsIngestCursor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("address", models.EmailField(max_length=254, unique=True)),
                ("uid_validity", models.PositiveBigIntegerField(default=0)),
                ("last_uid", models.PositiveBigIntegerField(default=0)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("processed_count", models.PositiveBigIntegerField(default=0)),
                ("rejected_count", models.PositiveBigIntegerField(default=0)),
            ],
            options={"db_table": "tls_ingest_cursor"},
        ),
        migrations.AddIndex(
            model_name="tlsaggregatereport",
            index=models.Index(fields=["tenant", "domain", "-period_end"], name="tls_tenant_domain_idx"),
        ),
        migrations.AddConstraint(
            model_name="tlsfailurebucket",
            constraint=models.UniqueConstraint(fields=("report", "result_type"), name="tls_unique_result_type"),
        ),
    ]
