# Generated for PostBox restore-to-original-folder provenance.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("postbox", "0006_remoteimagesendertrust"),
    ]

    operations = [
        migrations.CreateModel(
            name="MessageMoveProvenance",
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
                ("original_folder", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="postbox_move_provenance",
                        to="mailboxes.mailbox",
                    ),
                ),
            ],
            options={
                "db_table": "postbox_message_move_provenance",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="messagemoveprovenance",
            constraint=models.UniqueConstraint(
                fields=("mailbox", "message_key"),
                name="postbox_message_move_provenance_unique",
            ),
        ),
    ]
