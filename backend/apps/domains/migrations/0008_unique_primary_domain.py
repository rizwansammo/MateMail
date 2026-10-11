"""Install primary-domain uniqueness only AFTER the data backfill commits.

Production has existing Domain rows. Combining RunPython UPDATEs with CREATE
UNIQUE INDEX on the same table in one PostgreSQL migration transaction raises:
'cannot CREATE INDEX "domains_domain" because it has pending trigger events'.
The preceding migration commits first, clearing those pending trigger events.
"""
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("domains", "0007_primary_domain")]

    operations = [
        migrations.AddConstraint(
            model_name="domain",
            constraint=models.UniqueConstraint(
                fields=["tenant"],
                condition=Q(is_primary=True),
                name="uniq_primary_domain_per_tenant",
            ),
        ),
    ]
