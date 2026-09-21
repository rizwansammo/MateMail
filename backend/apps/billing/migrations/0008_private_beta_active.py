from django.db import migrations


def make_private_beta_active(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Subscription = apps.get_model("billing", "Subscription")
    Tenant = apps.get_model("tenants", "Tenant")

    Plan.objects.filter(
        tier="trial",
        display_name__in=["Trial", "Free Trial"],
    ).update(display_name="Private Beta")

    Subscription.objects.filter(status="trialing").update(
        status="active",
        trial_ends_at=None,
    )

    Tenant.objects.filter(status="trial").update(status="active")


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0007_seed_sending_limits"),
        ("tenants", "0003_backfill_approval"),
    ]

    operations = [
        migrations.RunPython(
            make_private_beta_active,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
