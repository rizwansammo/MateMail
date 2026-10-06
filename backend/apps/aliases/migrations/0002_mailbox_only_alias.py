from django.db import migrations, models
import django.db.models.deletion


def normalize_legacy_alias_destinations(apps, schema_editor):
    """
    Convert the old literal-destination shape only when it already points to a
    mailbox in the same organization.

    An external destination cannot be silently converted to Forwarding because
    Forwarding has different semantics and requires a source mailbox. Fail
    closed instead of changing customer mail flow.
    """
    Alias = apps.get_model("aliases", "Alias")
    Mailbox = apps.get_model("mailboxes", "Mailbox")

    for alias in Alias.objects.filter(destination_mailbox_id__isnull=True).iterator():
        destination = (alias.destination_address or "").strip().lower()
        mailbox = (
            Mailbox.objects
            .filter(tenant_id=alias.tenant_id, email__iexact=destination)
            .first()
        )
        if mailbox is None:
            raise RuntimeError(
                "A legacy Alias has no internal MateMail mailbox destination. "
                "Resolve that Alias before applying the mailbox-only Alias migration."
            )
        alias.destination_mailbox_id = mailbox.id
        alias.save(update_fields=["destination_mailbox"])


class Migration(migrations.Migration):

    dependencies = [
        ("aliases", "0001_initial"),
        ("mailboxes", "0002_mailbox_kind"),
    ]

    operations = [
        migrations.RunPython(
            normalize_legacy_alias_destinations,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="alias",
            name="destination_mailbox",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="incoming_aliases",
                to="mailboxes.mailbox",
            ),
        ),
        migrations.RemoveField(
            model_name="alias",
            name="destination_address",
        ),
    ]
