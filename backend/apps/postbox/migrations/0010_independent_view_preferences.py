"""Independent PostBox inbox list and opened-message reader preferences."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("postbox", "0009_folder_label_colors")]

    operations = [
        migrations.AddField(
            model_name="postboxpreference", name="list_view",
            field=models.CharField(
                max_length=13, choices=[
                    ("conversations", "Conversations"), ("messages", "Messages"),
                ], default="conversations",
            ),
        ),
        migrations.AddField(
            model_name="postboxpreference", name="reader_view",
            field=models.CharField(
                max_length=6, choices=[
                    ("thread", "Conversation"), ("single", "Single message"),
                ], default="thread",
            ),
        ),
    ]
