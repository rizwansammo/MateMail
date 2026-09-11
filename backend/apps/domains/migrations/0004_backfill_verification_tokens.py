"""
Issue a verification token to every pre-existing domain.

Deliberately does NOT mark anything verified. A domain that exists only because
someone typed it into MateMail before P3 has proved nothing about who controls
it — that is precisely the hole P3 closes. Existing domains therefore land in
PENDING with a token ready, and their owners verify like everyone else.

Consequence worth stating plainly: any domain already provisioned into a mail
engine becomes unprovisionable until verified. On this deployment that is
nobody, because MAIL_ENGINE_ADAPTER=stub and no engine exists. Were there real
provisioned domains, the safe order would be to verify them before deploying
this migration, not to grandfather them through.

Reversal clears the tokens; it cannot restore verification state, and the
schema migration behind it is what actually owns that.
"""
import secrets

from django.db import migrations

TOKEN_PREFIX = "matemail-verify"


def issue_tokens(apps, schema_editor):
    Domain = apps.get_model("domains", "Domain")
    # Only rows lacking a token. Re-running must not change a value a customer
    # may already have pasted into their DNS zone.
    pending = Domain.objects.filter(verification_token="")
    count = 0
    for domain in pending.iterator():
        domain.verification_token = f"{TOKEN_PREFIX}-{secrets.token_urlsafe(32)}"
        # ownership_status is left at its default ("pending") on purpose.
        domain.save(update_fields=["verification_token"])
        count += 1
    if count:
        print(f"\n    issued verification tokens to {count} existing domain(s), all left PENDING")


def clear_tokens(apps, schema_editor):
    Domain = apps.get_model("domains", "Domain")
    Domain.objects.update(verification_token="")


class Migration(migrations.Migration):

    dependencies = [
        ("domains", "0003_domain_ownership_verification"),
    ]

    operations = [
        migrations.RunPython(issue_tokens, clear_tokens),
    ]
