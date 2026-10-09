"""New PostBox defaults only. Existing stored user preferences are unchanged."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("postbox", "0015_scheduled_actor_requirement")]

    operations = [
        migrations.AlterField(
            model_name="postboxpreference",
            name="theme",
            field=models.CharField(
                choices=[("light", "Light"), ("dark", "Dark"), ("system", "System")],
                default="light",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="postboxpreference",
            name="density",
            field=models.CharField(
                choices=[
                    ("comfortable", "Comfortable"),
                    ("compact", "Compact"),
                    ("extra_compact", "Extra Compact"),
                ],
                default="extra_compact",
                max_length=13,
            ),
        ),
        migrations.AlterField(
            model_name="postboxpreference",
            name="reading_pane",
            field=models.CharField(
                choices=[("right", "Right"), ("bottom", "Bottom"), ("off", "Off")],
                default="off",
                max_length=8,
            ),
        ),
    ]
