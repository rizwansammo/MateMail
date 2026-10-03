# PostBox virtual labels (no additional IMAP copies).
import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Lower
import uuid


class Migration(migrations.Migration):
    dependencies = [("postbox", "0007_messagemoveprovenance")]

    operations = [
        migrations.CreateModel(
            name="MailLabel",
            fields=[
                ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ("name", models.CharField(max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("mailbox", models.ForeignKey(
                    to="mailboxes.mailbox", related_name="postbox_labels",
                    on_delete=django.db.models.deletion.CASCADE,
                )),
            ],
            options={"db_table": "postbox_label", "ordering": ["name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="maillabel",
            constraint=models.UniqueConstraint(
                Lower("name"), "mailbox", name="postbox_label_mailbox_name_ci",
            ),
        ),
        migrations.CreateModel(
            name="MessageLabel",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("message_key", models.CharField(max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("label", models.ForeignKey(
                    to="postbox.maillabel", related_name="assignments",
                    on_delete=django.db.models.deletion.CASCADE,
                )),
            ],
            options={"db_table": "postbox_message_label"},
        ),
        migrations.AddConstraint(
            model_name="messagelabel",
            constraint=models.UniqueConstraint(
                fields=["label", "message_key"],
                name="postbox_message_label_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="messagelabel",
            index=models.Index(fields=["message_key"], name="pb_label_msg_key_idx"),
        ),
    ]
