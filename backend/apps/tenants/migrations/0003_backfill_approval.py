"""
Data migration: grandfather workspaces that predate the approval gate.

P5 makes approval a real gate. `Tenant.can_use_mail` now requires both a
mail-enabled status AND an `approved_at` timestamp, and the new column arrives
NULL for every existing row. Without this migration the schema change would
silently revoke Mail Engine access from every workspace that already had it —
a policy change applied retroactively by a side effect, which is exactly the
kind of thing a migration must not do.

So a workspace that was already TRIAL or ACTIVE is recorded as approved at the
moment of the migration, with no approver and an explicit reason. `approved_by`
stays NULL because no human approved it and inventing one would corrupt the
audit trail; the reason text says so in as many words.

Everything else — pending, suspended, cancelled — is left untouched. Those
workspaces did not have mail access before this migration and do not gain any.

Production held zero tenants when this was written (verified, not assumed), so
in production this is a no-op. It exists for development and staging databases,
and so that the schema change is safe to apply anywhere.
"""
from django.db import migrations
from django.utils import timezone

#: Statuses that implied mail access before P5.
GRANDFATHERED = ("trial", "active")

REASON = (
    "Automatically approved during the P5 migration: this workspace existed "
    "and was active before platform approval became a requirement."
)


def grandfather_existing(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    now = timezone.now()
    Tenant.objects.filter(
        status__in=GRANDFATHERED, approved_at__isnull=True
    ).update(approved_at=now, review_reason=REASON)


def ungrandfather(apps, schema_editor):
    """
    Reverse by clearing only what this migration wrote.

    Matching on the reason text means a workspace a real admin approved after
    the migration keeps its approval, rather than being stripped by a rollback
    that had nothing to do with it.
    """
    Tenant = apps.get_model("tenants", "Tenant")
    Tenant.objects.filter(review_reason=REASON).update(
        approved_at=None, review_reason=""
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_tenant_approved_at_tenant_approved_by_and_more"),
    ]

    operations = [
        migrations.RunPython(grandfather_existing, reverse_code=ungrandfather),
    ]
