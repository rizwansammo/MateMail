"""Mailbox-owned folder colors and virtual-label colors; no IMAP data changes."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("postbox", "0008_virtual_labels")]

    operations = [
        migrations.AddField(
            model_name="maillabel",
            name="color",
            field=models.CharField(max_length=7, default="#9333ea"),
        ),
        migrations.CreateModel(
            name="FolderAppearance",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("color", models.CharField(max_length=7, default="#2563eb")),
                ("mailbox", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="postbox_folder_appearances",
                    to="mailboxes.mailbox",
                )),
            ],
            options={"db_table": "postbox_folder_appearance"},
        ),
        migrations.AddConstraint(
            model_name="folderappearance",
            constraint=models.UniqueConstraint(
                fields=["mailbox", "name"],
                name="postbox_folder_appearance_unique",
            ),
        ),
    ]
