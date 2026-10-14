"""Phase 6D: explicit, expiring system mailbox read grants.

No data migration, default grants, or privilege elevation: the table starts
empty on every deployment and no mailbox becomes readable by default.
"""
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("platform_admin", "0002_system_mail_drafts"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformSystemMailboxGrant",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("role", models.CharField(max_length=16, choices=[("security", "Security"), ("noreply", "Noreply")])),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("grantee", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="platform_system_mailbox_grants",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("granted_by", models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="approved_system_mailbox_grants",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"db_table": "platform_system_mailbox_grant"},
        ),
        migrations.AddConstraint(
            model_name="platformsystemmailboxgrant",
            constraint=models.UniqueConstraint(
                fields=("grantee", "role"),
                name="unique_platform_sensitive_mail_grant",
            ),
        ),
        migrations.AddConstraint(
            model_name="platformsystemmailboxgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(role__in=["security", "noreply"]),
                name="platform_sensitive_mail_role_allowlist",
            ),
        ),
    ]
