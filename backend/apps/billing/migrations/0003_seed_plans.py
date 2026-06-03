"""
Data migration: creates the four default plan records.
Platform admin can modify these via Django admin.
"""
from django.db import migrations


PLANS = [
    {
        "tier": "trial",
        "display_name": "Free Trial",
        "price_monthly": "0.00",
        "max_domains": 2,
        "max_mailboxes": 5,
        "max_members": 3,
        "max_storage_per_mailbox_mb": 512,
        "includes_spam_quarantine": False,
        "includes_audit_logs": False,
        "includes_queue_visibility": False,
        "includes_backup_controls": False,
        "includes_team_roles": False,
        "is_active": True,
    },
    {
        "tier": "starter",
        "display_name": "Starter",
        "price_monthly": "9.00",
        "max_domains": 5,
        "max_mailboxes": 10,
        "max_members": 5,
        "max_storage_per_mailbox_mb": 1024,
        "includes_spam_quarantine": True,
        "includes_audit_logs": True,
        "includes_queue_visibility": False,
        "includes_backup_controls": False,
        "includes_team_roles": False,
        "is_active": True,
    },
    {
        "tier": "business",
        "display_name": "Business",
        "price_monthly": "29.00",
        "max_domains": 20,
        "max_mailboxes": 50,
        "max_members": 20,
        "max_storage_per_mailbox_mb": 5120,
        "includes_spam_quarantine": True,
        "includes_audit_logs": True,
        "includes_queue_visibility": True,
        "includes_backup_controls": True,
        "includes_team_roles": True,
        "is_active": True,
    },
    {
        "tier": "infrastructure",
        "display_name": "Infrastructure",
        "price_monthly": "99.00",
        "max_domains": 100,
        "max_mailboxes": 500,
        "max_members": 100,
        "max_storage_per_mailbox_mb": 20480,
        "includes_spam_quarantine": True,
        "includes_audit_logs": True,
        "includes_queue_visibility": True,
        "includes_backup_controls": True,
        "includes_team_roles": True,
        "is_active": True,
    },
]


def seed_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for data in PLANS:
        Plan.objects.get_or_create(tier=data["tier"], defaults=data)


def unseed_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(tier__in=[p["tier"] for p in PLANS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_plan_trial_max_members"),
    ]

    operations = [
        migrations.RunPython(seed_plans, reverse_code=unseed_plans),
    ]
