import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("domains", "0006_domain_ownership_recheck_failures"),
        ("mailboxes", "0002_mailbox_kind"),
        ("tenants", "0004_customhostname"),
    ]

    operations = [
        migrations.CreateModel(
            name="ForwardGroup",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("local_part", models.CharField(db_index=True, max_length=64)),
                ("address", models.EmailField(db_index=True, max_length=254, unique=True)),
                ("display_name", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("active", "Active"), ("disabled", "Disabled")], default="active", max_length=20)),
                ("sender_policy", models.CharField(choices=[("anyone", "Anyone"), ("organization", "Organization only"), ("members", "Members only"), ("selected", "Selected senders")], default="anyone", max_length=20)),
                ("mail_engine_provisioned", models.BooleanField(default=False)),
                ("mail_engine_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("domain", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="forward_groups", to="domains.domain")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="forward_groups", to="tenants.tenant")),
            ],
            options={
                "db_table": "forward_groups_forward_group",
                "ordering": ["address"],
                "constraints": [
                    models.UniqueConstraint(fields=("tenant", "domain", "local_part"), name="uniq_forward_group_local_part")
                ],
            },
        ),
        migrations.CreateModel(
            name="ForwardGroupMember",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("role", models.CharField(choices=[("member", "Member"), ("owner", "Owner")], default="member", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="members", to="forward_groups.forwardgroup")),
                ("mailbox", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="forward_group_memberships", to="mailboxes.mailbox")),
            ],
            options={
                "db_table": "forward_groups_member",
                "ordering": ["mailbox__email"],
                "constraints": [
                    models.UniqueConstraint(fields=("group", "mailbox"), name="uniq_forward_group_member")
                ],
            },
        ),
        migrations.CreateModel(
            name="ForwardGroupAllowedSender",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="allowed_senders", to="forward_groups.forwardgroup")),
                ("mailbox", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="forward_group_sender_grants", to="mailboxes.mailbox")),
            ],
            options={
                "db_table": "forward_groups_allowed_sender",
                "ordering": ["mailbox__email"],
                "constraints": [
                    models.UniqueConstraint(fields=("group", "mailbox"), name="uniq_forward_group_allowed_sender")
                ],
            },
        ),
    ]
