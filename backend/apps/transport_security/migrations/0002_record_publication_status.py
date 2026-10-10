# DNS-Phase 2: distinguish permission to publish DNS from verification in public DNS.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("transport_security", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="domaintransportsecurity",
            name="dns_records_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="domaintransportsecurity",
            name="sts_txt_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="domaintransportsecurity",
            name="tls_rpt_txt_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
