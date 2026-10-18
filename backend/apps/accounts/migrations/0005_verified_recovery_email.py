"""Verified independent recovery email and one-time challenge storage."""
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_encrypt_existing_totp_secrets")]

    operations = [
        migrations.CreateModel(
            name="VerifiedRecoveryEmail",
            fields=[
                ("user", models.OneToOneField(
                    primary_key=True, to=settings.AUTH_USER_MODEL,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="recovery_contact", serialize=False,
                )),
                ("email", models.EmailField(blank=True, default="")),
                ("verified_at", models.DateTimeField(null=True, blank=True)),
                ("pending_email", models.EmailField(blank=True, default="")),
                ("challenge_hash", models.CharField(max_length=64, blank=True, default="")),
                ("code_hash", models.CharField(max_length=64, blank=True, default="")),
                ("expires_at", models.DateTimeField(null=True, blank=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"db_table": "accounts_verified_recovery_email"},
        ),
        migrations.CreateModel(
            name="RecoveryLoginChallenge",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4,
                                        editable=False, serialize=False)),
                ("user", models.ForeignKey(
                    to=settings.AUTH_USER_MODEL,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="recovery_login_challenges",
                )),
                ("challenge_hash", models.CharField(max_length=64, unique=True)),
                ("code_hash", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("consumed_at", models.DateTimeField(null=True, blank=True)),
            ],
            options={"db_table": "accounts_recovery_login_challenge"},
        ),
    ]
