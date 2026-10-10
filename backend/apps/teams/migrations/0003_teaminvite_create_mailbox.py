from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("teams", "0002_apikey_scopes")]
    operations = [
        migrations.AddField(model_name="teaminvite", name="create_mailbox",
                            field=models.BooleanField(default=False)),
    ]
