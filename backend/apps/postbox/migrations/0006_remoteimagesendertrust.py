# Generated for persistent PostBox remote-image sender preferences.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("postbox", "0005_postboxpushdevice_postboxpushevent"),
    ]

    operations = [
        migrations.CreateModel(
            name="RemoteImageSenderTrust",
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
                ("sender", models.EmailField(max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="remote_image_sender_trusts",
                        to="mailboxes.mailbox",
                    ),
                ),
            ],
            options={
                "db_table": "postbox_remote_image_sender_trust",
                "ordering": ["sender"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("mailbox", "sender"),
                        name="postbox_remote_image_sender_trust_unique",
                    )
                ],
            },
        ),
    ]
