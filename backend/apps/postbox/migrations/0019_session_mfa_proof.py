"""Add authenticated second-factor proof to each PostBox session."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("postbox", "0018_mailbox_two_factor")]
    operations = [
        migrations.AddField(
            model_name="postboxsession",
            name="mfa_verified_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
