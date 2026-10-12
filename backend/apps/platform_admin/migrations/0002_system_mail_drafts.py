"""Immutable Phase 3 system mail config drafts, with no activation."""
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("platform_admin", "0001_initial"),
        ("domains", "0009_mail_domain_ownership"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformSystemMailRevision",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("revision", models.PositiveIntegerField(unique=True)),
                ("domain", models.CharField(max_length=255)),
                ("sender_name", models.CharField(max_length=80, default="MateMail")),
                ("noreply_address", models.EmailField(max_length=254)),
                ("security_address", models.EmailField(max_length=254)),
                ("operations_address", models.EmailField(max_length=254)),
                ("dmarc_address", models.EmailField(max_length=254)),
                ("tlsrpt_address", models.EmailField(max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="system_mail_config_revisions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "platform_system_mail_revision", "ordering": ["-revision"]},
        ),
        migrations.CreateModel(
            name="PlatformSystemMailPointer",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("revision", models.PositiveIntegerField(default=0)),
                ("draft", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="platform_admin.platformsystemmailrevision")),
            ],
            options={"db_table": "platform_system_mail_pointer"},
        ),
        migrations.AddConstraint(
            model_name="platformsystemmailpointer",
            constraint=models.CheckConstraint(condition=models.Q(id=1), name="system_mail_pointer_singleton"),
        ),
    ]
