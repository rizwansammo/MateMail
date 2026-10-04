from django.db import migrations, models


def backfill_structured_rules(apps, schema_editor):
    Rule = apps.get_model("postbox", "MailRule")
    for rule in Rule.objects.all().iterator():
        condition = {
            "field": rule.field,
            "match": rule.match,
            "value": rule.value,
        }
        action = {"action": rule.action}
        if rule.action_folder:
            action["folder"] = rule.action_folder
        Rule.objects.filter(pk=rule.pk).update(
            condition_mode="all",
            conditions=[condition],
            actions=[action],
        )


class Migration(migrations.Migration):
    dependencies = [("postbox", "0010_independent_view_preferences")]

    operations = [
        migrations.AddField(
            model_name="mailrule",
            name="condition_mode",
            field=models.CharField(
                choices=[("all", "Match all conditions"), ("any", "Match any condition")],
                default="all",
                max_length=3,
            ),
        ),
        migrations.AddField(
            model_name="mailrule",
            name="conditions",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="mailrule",
            name="actions",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name="mailrule",
            name="field",
            field=models.CharField(
                choices=[
                    ("from", "From"), ("to", "To or Cc"), ("subject", "Subject"),
                    ("sender_domain", "Sender domain"), ("mailing_list", "Mailing list"),
                    ("body", "Message body"), ("message_size", "Message size"),
                    ("has_attachment", "Has attachment"), ("attachment_name", "Attachment name"),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="mailrule",
            name="match",
            field=models.CharField(
                choices=[
                    ("contains", "contains"), ("is", "is exactly"),
                    ("not_contains", "does not contain"), ("not_is", "is not exactly"),
                    ("over", "is over"), ("under", "is under"),
                ],
                default="contains",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="mailrule",
            name="action",
            field=models.CharField(
                choices=[
                    ("move", "Move to folder"), ("copy", "Copy to folder"),
                    ("archive", "Archive"), ("star", "Star"),
                    ("mark_read", "Mark as read"), ("mark_unread", "Mark as unread"),
                    ("delete", "Move to Trash"),
                ],
                max_length=16,
            ),
        ),
        migrations.RunPython(backfill_structured_rules, migrations.RunPython.noop),
    ]
