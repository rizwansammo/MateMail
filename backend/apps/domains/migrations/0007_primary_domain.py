from django.db import migrations, models


def backfill_primary(apps, schema_editor):
    Domain = apps.get_model("domains", "Domain")
    # Existing tenants get a deterministic, eligible initial preference.
    # This makes no changes to mailboxes or to mail routing/DNS records.
    tenant_ids = Domain.objects.filter(
        ownership_status="verified", status="active"
    ).order_by().values_list("tenant_id", flat=True).distinct()
    for tenant_id in tenant_ids.iterator():
        first = (Domain.objects.filter(
            tenant_id=tenant_id, ownership_status="verified", status="active"
        ).order_by("added_at", "id").first())
        if first:
            Domain.objects.filter(pk=first.pk).update(is_primary=True)


class Migration(migrations.Migration):
    dependencies = [("domains", "0006_domain_ownership_recheck_failures")]
    # Backfill writes to domains_domain. Postgres requires a COMMIT before
    # CREATE INDEX on this table due to pending trigger events. The uniqueness
    # index is installed by 0008, its own atomic migration.

    operations = [
        migrations.AddField(
            model_name="domain",
            name="is_primary",
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.RunPython(backfill_primary, migrations.RunPython.noop),
    ]
