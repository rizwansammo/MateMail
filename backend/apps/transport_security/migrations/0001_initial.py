# P4-C.B additive configuration only; no existing mail or DNS table changes.
import django.db.models.deletion
from django.db import migrations, models
import apps.transport_security.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("domains", "0006_domain_ownership_recheck_failures"),
    ]

    operations = [
        migrations.CreateModel(
            name="DomainTransportSecurity",
            fields=[
                ("domain", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    primary_key=True, related_name="transport_security",
                    serialize=False, to="domains.domain",
                )),
                ("enabled", models.BooleanField(default=False)),
                ("lifecycle", models.CharField(
                    choices=[
                        ("disabled", "Disabled"),
                        ("pending_dns", "Waiting for DNS and policy hosting"),
                        ("provisioning", "Provisioning"),
                        ("ready", "HTTPS verified, DNS publication pending"),
                        ("active", "Active"),
                        ("error", "Provisioning error"),
                        ("deactivating", "Deactivating"),
                    ],
                    default="disabled", max_length=20,
                )),
                ("certificate_status", models.CharField(
                    choices=[
                        ("not_requested", "Not requested"),
                        ("issuing", "Issuing"),
                        ("active", "Active"),
                        ("error", "Error"),
                        ("revoked", "Revoked"),
                    ],
                    default="not_requested", max_length=20,
                )),
                ("policy_id", models.CharField(
                    default=apps.transport_security.models.new_policy_id,
                    editable=False, max_length=24,
                )),
                ("dns_verified_at", models.DateTimeField(blank=True, null=True)),
                ("cert_verified_at", models.DateTimeField(blank=True, null=True)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "transport_security_configuration"},
        ),
    ]
