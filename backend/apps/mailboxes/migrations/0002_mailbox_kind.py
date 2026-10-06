from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mailboxes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mailbox",
            name="kind",
            field=models.CharField(
                choices=[
                    ("personal", "Personal mailbox"),
                    ("team_box", "TeamBox"),
                ],
                db_index=True,
                default="personal",
                max_length=20,
            ),
        ),
    ]
