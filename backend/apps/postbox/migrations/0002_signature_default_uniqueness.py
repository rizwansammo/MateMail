from django.db import migrations, models


def collapse_duplicate_defaults(apps, schema_editor):
    MailSignature = apps.get_model("postbox", "MailSignature")

    for field in ("use_for_new", "use_for_replies"):
        mailbox_ids = (
            MailSignature.objects.filter(**{field: True})
            .values_list("mailbox_id", flat=True)
            .distinct()
        )
        for mailbox_id in mailbox_ids:
            defaults = list(
                MailSignature.objects.filter(
                    mailbox_id=mailbox_id,
                    **{field: True},
                ).order_by("-updated_at", "-created_at", "id")
            )
            if len(defaults) <= 1:
                continue
            keep = defaults[0]
            MailSignature.objects.filter(
                mailbox_id=mailbox_id,
                **{field: True},
            ).exclude(pk=keep.pk).update(**{field: False})


class Migration(migrations.Migration):

    dependencies = [
        ("postbox", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            collapse_duplicate_defaults,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="mailsignature",
            constraint=models.UniqueConstraint(
                condition=models.Q(use_for_new=True),
                fields=("mailbox",),
                name="postbox_signature_one_default_new",
            ),
        ),
        migrations.AddConstraint(
            model_name="mailsignature",
            constraint=models.UniqueConstraint(
                condition=models.Q(use_for_replies=True),
                fields=("mailbox",),
                name="postbox_signature_one_default_replies",
            ),
        ),
    ]
