"""Quorum verified owner emergency cases. No automatic recovery on migrate."""
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("platform_admin", "0003_sensitive_mailbox_grants"),
        ("accounts", "0006_hub_mfa_recovery_account_guards"),
        ("tenants", "0008_admin_mfa_recovery_ticket"),
    ]

    operations = [
        migrations.CreateModel(
            name="OwnerEmergencyRecoveryCase",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("mode", models.CharField(max_length=8, choices=[
                    ("mfa", "Reset authenticator"),
                    ("full", "Reset password and authenticator"),
                ])),
                ("reason", models.CharField(max_length=500)),
                ("identity_evidence_reference", models.CharField(max_length=200)),
                ("identity_verification_note", models.CharField(max_length=500, blank=True)),
                ("reviewer_verification_note", models.CharField(max_length=500, blank=True)),
                ("recovery_address", models.EmailField(max_length=254)),
                ("recovery_address_verified_at", models.DateTimeField()),
                ("owner_security_epoch", models.PositiveIntegerField()),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("hold_until", models.DateTimeField(blank=True, null=True)),
                ("capability_hash", models.CharField(max_length=64, blank=True)),
                ("dispatched_at", models.DateTimeField(blank=True, null=True)),
                ("capability_expires_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("approved_by", models.ForeignKey(
                    to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT,
                    null=True, blank=True, related_name="approved_owner_emergency_cases",
                )),
                ("initiated_by", models.ForeignKey(
                    to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT,
                    related_name="initiated_owner_emergency_cases",
                )),
                ("owner", models.ForeignKey(
                    to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.PROTECT,
                    related_name="owner_emergency_cases",
                )),
                ("tenant", models.ForeignKey(
                    to="tenants.tenant", on_delete=django.db.models.deletion.PROTECT,
                    related_name="owner_emergency_cases",
                )),
            ],
            options={
                "db_table": "platform_owner_emergency_recovery",
                "indexes": [models.Index(fields=["tenant", "-created_at"],
                                         name="owner_emergency_tenant_idx")],
            },
        ),
    ]
