"""Phase 7A encrypted mailbox-level 2FA state; no enrollment API activation."""
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("postbox", "0017_mail_client_access_policy"),
        ("mailboxes", "0002_mailbox_kind"),
    ]

    operations = [
        migrations.CreateModel(
            name="MailboxTwoFactorSetup",
            fields=[
                ("mailbox", models.OneToOneField(
                    to="mailboxes.mailbox",
                    on_delete=django.db.models.deletion.CASCADE,
                    primary_key=True, related_name="two_factor_setup",
                    serialize=False,
                )),
                ("totp_secret_encrypted", models.TextField()),
                ("enabled_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "postbox_mailbox_two_factor"},
        ),
        migrations.CreateModel(
            name="MailboxTwoFactorBackupCode",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("mailbox", models.ForeignKey(
                    to="mailboxes.mailbox",
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="two_factor_backup_codes",
                )),
                ("code_hash", models.CharField(max_length=64)),
                ("used_at", models.DateTimeField(null=True, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "postbox_mailbox_two_factor_backup"},
        ),
        migrations.AddConstraint(
            model_name="mailboxtwofactorbackupcode",
            constraint=models.UniqueConstraint(
                fields=("mailbox", "code_hash"),
                name="unique_postbox_mfa_recovery_digest",
            ),
        ),
    ]
