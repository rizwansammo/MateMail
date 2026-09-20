# Generated for the first-party connected-app boundary.
import uuid

import apps.integrations.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tenants", "0003_backfill_approval"),
        ("mailboxes", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Integration",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("secret_prefix", models.CharField(max_length=16)),
                ("secret_hash", models.CharField(max_length=64, unique=True)),
                (
                    "permissions",
                    models.JSONField(
                        default=apps.integrations.models.default_permissions
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_integrations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="integrations",
                        to="mailboxes.mailbox",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="integrations",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "integrations_integration",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ConnectionRequest",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("request_hash", models.CharField(max_length=64, unique=True)),
                ("poll_hash", models.CharField(max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("rejected_at", models.DateTimeField(blank=True, null=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_integration_connections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "integration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="connection_requests",
                        to="integrations.integration",
                    ),
                ),
            ],
            options={"db_table": "integrations_connection_request"},
        ),
        migrations.CreateModel(
            name="AccessToken",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("token_prefix", models.CharField(max_length=16)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "integration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access_tokens",
                        to="integrations.integration",
                    ),
                ),
            ],
            options={"db_table": "integrations_access_token"},
        ),
    ]
