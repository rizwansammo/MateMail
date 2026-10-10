# DNS-Phase 3: durable opt-out, DNS absence and cache-drain checkpoints.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("transport_security", "0002_record_publication_status")]
    operations = [
        migrations.AlterField(
            model_name="domaintransportsecurity", name="lifecycle",
            field=models.CharField(max_length=20, default="disabled", choices=[
                ("disabled", "Disabled"),
                ("pending_dns", "Waiting for DNS and policy hosting"),
                ("provisioning", "Provisioning"),
                ("ready", "HTTPS verified, DNS publication pending"),
                ("active", "Active"),
                ("error", "Provisioning error"),
                ("deactivating", "Replacing HTTPS policy with mode none"),
                ("draining", "Awaiting customer DNS removal and MTA-STS cache expiry"),
            ]),
        ),
        migrations.AddField(model_name="domaintransportsecurity", name="deactivation_requested_at",
                            field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name="domaintransportsecurity", name="deactivation_policy_none_at",
                            field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name="domaintransportsecurity", name="deactivation_dns_absent_since",
                            field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name="domaintransportsecurity", name="deactivation_completed_at",
                            field=models.DateTimeField(null=True, blank=True)),
    ]
