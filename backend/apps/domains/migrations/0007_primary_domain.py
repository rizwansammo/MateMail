from django.db import migrations, models
from django.db.models import Q


def backfill_primary(apps, schema_editor):
    Domain = apps.get_model("domains", "Domain")
    # Existing tenants get a deterministic, eligible initial preference.
    # This makes no changes to mailboxes or to mail routing/DNS records.
    tenant_ids = Domain.objects.filter(
        ownership_status="verified", status="active"
    ).values_list("tenant_id", flat=True).distinct()
    for tenant_id in tenant_ids.iterator():
        first = (Domain.objects.filter(
            tenant_id=tenant_id, ownership_status="verified", status="active"
        ).order_by("added_at", "id").first())
        if first:
            Domain.objects.filter(pk=first.pk).update(is_primary=True)


class Migration(migrations.Migration):
    dependencies = [("domains", "0006_domain_ownership_recheck_failures")]

    operations = [
        migrations.AddField(
            model_name="domain",
            name="is_primary",
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.RunPython(backfill_primary, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="domain",
            constraint=models.UniqueConstraint(
                fields=["tenant"],
                condition=Q(is_primary=True),
                name="uniq_primary_domain_per_tenant",
            ),
        ),
    ]
