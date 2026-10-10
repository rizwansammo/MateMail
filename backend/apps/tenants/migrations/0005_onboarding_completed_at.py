"""Persist one-time Hub onboarding completion.

The data backfill records already fully configured organizations as complete,
including those that no longer need to see Getting Started immediately after
upgrading. New organizations start with NULL and are latched only when all
initial requirements are observed.

This migration changes NO mail, DNS, provisioning or tenant access behavior.
"""
from django.db import migrations, models
from django.utils import timezone


def mark_existing_completed(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    Domain = apps.get_model("domains", "Domain")
    Mailbox = apps.get_model("mailboxes", "Mailbox")
    alias = schema_editor.connection.alias

    ready_domain_tenant_ids = Domain.objects.using(alias).filter(
        status="active",
        ownership_status="verified",
    ).values("tenant_id")

    ready_mailbox_tenant_ids = Mailbox.objects.using(alias).filter(
        tenant_id__in=ready_domain_tenant_ids,
    ).values("tenant_id")

    Tenant.objects.using(alias).filter(
        id__in=ready_mailbox_tenant_ids,
        onboarding_completed_at__isnull=True,
    ).update(onboarding_completed_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0004_customhostname"),
        ("domains", "0006_domain_ownership_recheck_failures"),
        ("mailboxes", "0002_mailbox_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="onboarding_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_completed, migrations.RunPython.noop),
    ]
