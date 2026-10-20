"""Safely add account-wide session revocation and forced MFA setup fields."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_verified_recovery_email")]
    operations = [
        migrations.AddField(
            model_name="user", name="hub_security_epoch",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user", name="hub_mfa_reenrollment_required",
            field=models.BooleanField(default=False),
        ),
    ]
