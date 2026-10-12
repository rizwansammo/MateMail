"""Reserve platform identities and register existing verified organization domains.

No provisioning, DNS, DKIM, mailbox or existing customer domain updates.
Abort atomically instead of claiming a name already verified by an organization.
"""
from django.db import migrations, models
import django.db.models.deletion
import uuid


def backfill_ownership(apps, schema_editor):
    Domain = apps.get_model("domains", "Domain")
    Registry = apps.get_model("domains", "MailDomainOwnership")
    alias = schema_editor.connection.alias
    protected = ("mail.matemail.pro", "system.matemail.pro", "mx.matemail.pro", "platform.matemail.pro", "portal.matemail.pro", "postbox.matemail.pro", "custom.matemail.pro", "autodiscover.matemail.pro", "admin.matemail.pro", "www.matemail.pro")
    if Domain.objects.using(alias).filter(
        domain__in=protected, ownership_status="verified",
    ).exists():
        raise RuntimeError(
            "A platform identity is verified under a tenant; stop for manual ownership review."
        )
    for name in protected:
        Registry.objects.using(alias).create(domain=name, owner_kind="platform")
    for row in Domain.objects.using(alias).filter(
        ownership_status="verified",
    ).iterator():
        Registry.objects.using(alias).create(
            domain=row.domain.strip().rstrip(".").lower(),
            owner_kind="organization", tenant_id=row.tenant_id,
            source_domain_id=row.pk,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("domains", "0008_unique_primary_domain"),
        ("tenants", "0005_onboarding_completed_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="MailDomainOwnership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("domain", models.CharField(max_length=255, unique=True)),
                ("owner_kind", models.CharField(choices=[("platform", "Platform"), ("organization", "Organization")], max_length=16)),
                ("source_domain_id", models.UUIDField(blank=True, null=True)),
                ("registered_at", models.DateTimeField(auto_now_add=True)),
                ("tenant", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name="mail_domain_ownerships", to="tenants.tenant",
                )),
            ],
            options={"db_table": "domains_mail_domain_ownership"},
        ),
        migrations.AddConstraint(
            model_name="maildomainownership",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(owner_kind="platform", tenant__isnull=True, source_domain_id__isnull=True)
                    | models.Q(owner_kind="organization", tenant__isnull=False, source_domain_id__isnull=False)
                ),
                name="mail_domain_owner_scope_valid",
            ),
        ),
        migrations.RunPython(backfill_ownership, migrations.RunPython.noop),
    ]
