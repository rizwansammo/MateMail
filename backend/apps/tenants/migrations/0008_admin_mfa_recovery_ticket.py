"""Persist time-limited, recipient-bound Hub Admin MFA recovery requests."""
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_hub_mfa_recovery_account_guards"),
        ("tenants", "0007_admin_hub_mfa_policy"),
    ]
    operations = [
        migrations.CreateModel(
            name="AdminHubMfaRecoveryTicket",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)),
                ("nonce_hash", models.CharField(max_length=64, unique=True)),
                ("recipient_email", models.EmailField(max_length=254)),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(null=True, blank=True)),
                ("cancelled_at", models.DateTimeField(null=True, blank=True)),
                ("target_membership", models.ForeignKey(
                    to="tenants.tenantmembership", on_delete=django.db.models.deletion.CASCADE,
                    related_name="hub_mfa_recovery_tickets",
                )),
                ("requested_by", models.ForeignKey(
                    to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT,
                    related_name="requested_hub_admin_mfa_recoveries",
                )),
            ],
            options={
                "db_table": "tenants_admin_hub_mfa_recovery",
                "indexes": [
                    models.Index(fields=["target_membership", "-created_at"],
                                 name="hub_mfa_target_created_idx"),
                ],
            },
        ),
    ]
