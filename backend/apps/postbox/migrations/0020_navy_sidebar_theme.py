from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("postbox", "0019_session_mfa_proof"),
    ]

    operations = [
        migrations.AlterField(
            model_name="postboxpreference",
            name="theme",
            field=models.CharField(
                choices=[("light", "Light"), ("dark", "Dark"), ("system", "System"), ("navy", "Navy sidebar")],
                default="light",
                max_length=10,
            ),
        ),
    ]
