from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("integrations", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="IntegrationDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("idempotency_key", models.CharField(max_length=100)),
                ("status", models.CharField(default="processing", max_length=16)),
                ("message_id", models.CharField(blank=True, default="", max_length=998)),
                ("filed_in_sent", models.BooleanField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("integration", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deliveries", to="integrations.integration")),
            ],
            options={
                "db_table": "integrations_delivery",
                "constraints": [
                    models.UniqueConstraint(fields=("integration", "idempotency_key"), name="uniq_integration_delivery_key")
                ],
            },
        ),
    ]
