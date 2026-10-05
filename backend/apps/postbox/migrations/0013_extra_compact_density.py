"""Add the opt-in Extra Compact PostBox density preset."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("postbox", "0012_messageorigin")]

    operations = [
        migrations.AlterField(
            model_name="postboxpreference",
            name="density",
            field=models.CharField(
                choices=[
                    ("comfortable", "Comfortable"),
                    ("compact", "Compact"),
                    ("extra_compact", "Extra Compact"),
                ],
                default="comfortable",
                max_length=13,
            ),
        ),
    ]
