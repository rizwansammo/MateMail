"""Add non-enforcing, per-membership Hub administrator MFA policy.

Existing users are OFF. This migration must not change authentication,
PostBox mailbox MFA, organization-wide policy or SMTP/IMAP access.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0006_organization_two_factor_policy")]

    operations = [
        migrations.AddField(
            model_name="tenantmembership",
            name="admin_hub_mfa_policy",
            field=models.CharField(
                max_length=12,
                choices=[
                    ("off", "Off"),
                    ("optional", "Optional"),
                    ("required", "Required"),
                ],
                default="off",
            ),
        ),
    ]
