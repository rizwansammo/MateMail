from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        # Add max_members to Plan
        migrations.AddField(
            model_name="plan",
            name="max_members",
            field=models.PositiveIntegerField(default=5),
        ),
        # Update tier field to include trial choice
        migrations.AlterField(
            model_name="plan",
            name="tier",
            field=models.CharField(
                choices=[
                    ("trial", "Trial"),
                    ("starter", "Starter"),
                    ("business", "Business"),
                    ("infrastructure", "Infrastructure"),
                ],
                max_length=30,
                unique=True,
            ),
        ),
        # Make price_monthly optional (default 0 for trial)
        migrations.AlterField(
            model_name="plan",
            name="price_monthly",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=8),
        ),
    ]
