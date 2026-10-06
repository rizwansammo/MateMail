from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("postbox", "0014_teambox_active_mailbox"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduledmessage",
            name="requires_submission_mailbox",
            field=models.BooleanField(default=False),
        ),
    ]
