from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("domains", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="domain",
            name="dkim_private_key",
            field=models.TextField(blank=True),
        ),
    ]
