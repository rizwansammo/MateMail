from django.db import migrations


def backfill_address_claims(apps, schema_editor):
    AddressClaim = apps.get_model("mail_directory", "AddressClaim")
    Mailbox = apps.get_model("mailboxes", "Mailbox")
    Alias = apps.get_model("aliases", "Alias")

    seen = set()

    for mailbox in Mailbox.objects.all().iterator():
        address = mailbox.email.strip().lower()
        if address in seen:
            raise RuntimeError(
                "Existing MateMail addresses collide case-insensitively; "
                "resolve the collision before applying the collaboration migration."
            )
        seen.add(address)
        AddressClaim.objects.create(
            tenant_id=mailbox.tenant_id,
            domain_id=mailbox.domain_id,
            address=address,
            kind="team_box" if mailbox.kind == "team_box" else "mailbox",
        )

    for alias in Alias.objects.all().iterator():
        address = alias.source_address.strip().lower()
        if address in seen:
            raise RuntimeError(
                "An existing Alias conflicts with another MateMail address; "
                "resolve the collision before applying the collaboration migration."
            )
        seen.add(address)
        AddressClaim.objects.create(
            tenant_id=alias.tenant_id,
            domain_id=alias.domain_id,
            address=address,
            kind="alias",
        )


def reverse_backfill(apps, schema_editor):
    AddressClaim = apps.get_model("mail_directory", "AddressClaim")
    AddressClaim.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("aliases", "0001_initial"),
        ("mail_directory", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_address_claims, reverse_backfill),
    ]
