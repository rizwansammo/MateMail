"""
Data migration: give each plan deliberate alias and outbound sending limits.

WHY THESE NUMBERS ARE PER-TIER
    The three limits they replace were literals inside the rate limiter —
    100/hour per mailbox, 500/hour per domain, 2000/day per tenant — identical
    for a free beta workspace and a future enterprise plan, and invisible to
    the product. Sending volume is a commercial dimension, so it belongs on the
    plan.

WHY THEY ARE CONSERVATIVE
    Every workspace sends from sending infrastructure whose reputation all of
    them share. A workspace that turns out to need more can ask and be given
    more in seconds; a workspace that discovers it can already send thousands
    before anyone notices costs every other customer their deliverability. The
    trial tier in particular is free and admin-approved during the Private
    Beta, and 50 messages an hour is ample for the business mail it is for.

    These are MateMail's application-level limits, enforced by the SMTP policy
    bridge. The engine holds its own per-mailbox limit as well, so a client
    that somehow reaches submission without us is still capped.

ALIASES
    An alias is a free forwarding address, which makes it the cheapest way to
    turn one approved workspace into many sending identities. Capped per tier
    at a level that comfortably covers ordinary use (role addresses, a few
    per mailbox) and stops bulk minting.

SAFETY
    All three columns are new, so every row holds exactly its field default and
    there is nothing an operator could have customised yet. The guard is kept
    anyway: a plan whose value already differs from the field default is left
    alone and the migration stays idempotent if it is ever re-run against a
    database an operator has since tuned. An unknown tier is left at the field
    defaults, which are the tightest values here — a plan nobody has classified
    does not get the most generous limits by accident.

    Production held zero subscriptions and four unmodified plans when this was
    written (verified, not assumed).
"""
from django.db import migrations

#: What the model declares. A plan still holding these has not been customised.
FIELD_DEFAULTS = {
    "max_aliases": 50,
    "max_messages_per_hour_per_mailbox": 50,
    "max_messages_per_day_per_tenant": 500,
}

#: tier -> (aliases, messages/hour/mailbox, messages/day/tenant)
#:
#: The daily workspace figure is deliberately far below mailboxes x hourly x 24:
#: it is a ceiling on what the workspace as a whole does in a day, not a sum of
#: what every mailbox could theoretically do.
TARGETS = {
    # Private Beta: free, admin-approved, 5 mailboxes. Field defaults, stated
    # explicitly so the beta policy is visible here rather than implied.
    "trial": (50, 50, 500),
    "starter": (100, 100, 1_000),
    "business": (500, 200, 5_000),
    "infrastructure": (2_000, 300, 20_000),
}


def set_sending_limits(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")

    for plan in Plan.objects.all():
        target = TARGETS.get(plan.tier)
        if target is None:
            # An unrecognised tier keeps the field defaults: the tightest
            # values in this file. Guessing generous limits for a plan nobody
            # has classified is how a sending reputation gets spent.
            continue

        values = dict(zip(FIELD_DEFAULTS, target))
        changed = []
        for field, value in values.items():
            # Only write where the plan still holds the field default.
            if getattr(plan, field) == FIELD_DEFAULTS[field]:
                setattr(plan, field, value)
                changed.append(field)
        if changed:
            plan.save(update_fields=changed)


def unset_sending_limits(apps, schema_editor):
    """
    Return every known tier to the field defaults.

    The columns themselves are removed by the schema migration, so this matters
    only when 0007 is reversed on its own.
    """
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(tier__in=TARGETS).update(**FIELD_DEFAULTS)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0006_plan_max_aliases_and_more"),
    ]

    operations = [
        migrations.RunPython(set_sending_limits, reverse_code=unset_sending_limits),
    ]
