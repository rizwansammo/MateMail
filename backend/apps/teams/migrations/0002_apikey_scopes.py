"""
Give every API key an explicit scope list.

Before this, a key authenticated as the user who created it and inherited that
user's permissions — including `is_platform_admin`. Scopes make a key a
credential in its own right.

Existing keys become `["read"]`: the column default applies to every current
row, so no key silently keeps write access it was never explicitly granted.
Any existing integration that writes through an API key will start receiving
403 and needs its scopes granted deliberately. That is the intended direction —
the alternative is to grandfather in exactly the privilege this migration
exists to remove.
"""
import apps.teams.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('teams', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='apikey',
            name='scopes',
            field=models.JSONField(default=apps.teams.models.default_scopes),
        ),
    ]
