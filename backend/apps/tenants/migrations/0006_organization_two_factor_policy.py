"""Phase 7A: introduce a non-enforcing organization MFA policy field.

Existing organizations stay OFF; this migration never changes login, mailbox
authentication, Native Engine credentials or production feature flags.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0005_onboarding_completed_at")]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="two_factor_policy",
            field=models.CharField(
                max_length=12,
                choices=[
                    ("off", "Off"),
                    ("encourage", "Encourage 2FA"),
                    ("require", "Require 2FA"),
                ],
                default="off",
            ),
        ),
    ]
