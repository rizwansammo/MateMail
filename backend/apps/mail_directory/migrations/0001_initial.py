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
            name="AddressClaim",
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
                ("address", models.EmailField(db_index=True, max_length=254, unique=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("mailbox", "Mailbox"),
                            ("team_box", "TeamBox"),
                            ("alias", "Alias"),
                            ("forward_group", "Forward Group"),
                        ],
                        max_length=24,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "domain",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mail_address_claims",
                        to="domains.domain",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mail_address_claims",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "mail_directory_address_claim",
                "ordering": ["address"],
            },
        ),
        migrations.CreateModel(
            name="MailboxAccessGrant",
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
                (
                    "grant_type",
                    models.CharField(
                        choices=[
                            ("team_box", "TeamBox membership"),
                            ("delegation", "Mailbox delegation"),
                        ],
                        max_length=20,
                    ),
                ),
                ("can_read", models.BooleanField(default=True)),
                ("can_manage", models.BooleanField(default=False)),
                ("can_send_as", models.BooleanField(default=False)),
                ("can_send_on_behalf", models.BooleanField(default=False)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "grantee_mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access_grants",
                        to="mailboxes.mailbox",
                    ),
                ),
                (
                    "target_mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access_grants_received",
                        to="mailboxes.mailbox",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mailbox_access_grants",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "mail_directory_access_grant",
                "ordering": ["target_mailbox", "grantee_mailbox"],
                "indexes": [
                    models.Index(
                        fields=["grantee_mailbox", "active"],
                        name="mail_access_grantee_idx",
                    ),
                    models.Index(
                        fields=["target_mailbox", "active"],
                        name="mail_access_target_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("target_mailbox", "grantee_mailbox"),
                        name="uniq_mailbox_access_pair",
                    ),
                ],
            },
        ),
    ]
