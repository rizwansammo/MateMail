# Generated for PostBox sent-message semantic origin tracking.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("postbox", "0011_rules_v2"),
    ]

    operations = [
        migrations.CreateModel(
            name="MessageOrigin",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("message_key", models.CharField(max_length=80)),
                (
                    "origin_role",
                    models.CharField(
                        choices=[("sent", "Sent")],
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="postbox_message_origins",
                        to="mailboxes.mailbox",
                    ),
                ),
            ],
            options={
                "db_table": "postbox_message_origin",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="messageorigin",
            constraint=models.UniqueConstraint(
                fields=("mailbox", "message_key"),
                name="postbox_message_origin_unique",
            ),
        ),
    ]
