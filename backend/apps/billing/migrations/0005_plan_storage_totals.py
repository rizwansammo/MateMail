"""
Data migration: give every plan a valid, deliberate storage triple.

WHY THIS EXISTS
    The Mail Engine refuses a domain whose storage numbers contradict each
    other, and it enforces all three at once:

        default_storage_per_mailbox_mb <= max_storage_per_mailbox_mb
                                       <= max_storage_total_mb

    Before this migration no plan could satisfy that, because two of the three
    numbers did not exist. The adapter sent a hard-coded domain quota of 0 with
    a positive per-mailbox ceiling, and the engine rejected every single domain
    creation with `mailbox_quota_exceeds_domain_quota`. Found by activating the
    real engine, not by any test — the fake engine accepted anything.

WHAT IT SETS

    trial — the current Private Beta plan.
        MateMail is free and admin-approved during the beta, with 1 GB
        mailboxes. Per-mailbox rises from 512 MB to 1024 MB to match that
        policy, and the total is mailboxes x 1 GB.

    starter / business / infrastructure — the commercial ladder.
        Deliberately NOT flattened to the beta policy. These are unused
        (zero subscriptions) but they are the intended future pricing, and
        rewriting Business from 5 GB mailboxes to 1 GB would be discarding a
        product decision this migration has no business making. Their existing
        per-mailbox ceilings are preserved; they gain a 1 GB starting default
        (MateMail's current default mailbox size, and never above their own
        ceiling) and a total that lets every mailbox reach its ceiling.

    The totals differ in KIND between the two groups, which is the point of
    storing the number rather than deriving it: the beta plan's pool is
    mailboxes x 1 GB, the ladder's is mailboxes x their own ceiling, and a
    future plan may be a genuinely shared pool smaller than either.

SAFETY
    Values are written per tier, only where the plan still holds its seeded
    values. A plan an operator has already customised is left alone and
    reported, because overwriting a deliberate commercial limit is worse than
    leaving one plan needing manual attention.
"""
from django.db import migrations

#: The Private Beta policy: free, admin-approved, 1 GB per mailbox.
BETA_MAILBOX_MB = 1024

#: tier -> (default_per_mailbox, max_per_mailbox, total)
#: `None` for max_per_mailbox means "keep whatever the plan already has".
TARGETS = {
    # The beta plan. Per-mailbox raised to the 1 GB policy; pool = boxes x 1 GB.
    "trial": (BETA_MAILBOX_MB, BETA_MAILBOX_MB, None),
    # The ladder. Ceilings preserved; pool = boxes x their own ceiling.
    "starter": (BETA_MAILBOX_MB, None, None),
    "business": (BETA_MAILBOX_MB, None, None),
    "infrastructure": (BETA_MAILBOX_MB, None, None),
}

#: What the seed migration wrote. A plan still matching this has not been
#: customised, so it is safe to update.
SEEDED_PER_MAILBOX_MB = {
    "trial": 512,
    "starter": 1024,
    "business": 5120,
    "infrastructure": 20480,
}


def set_storage_totals(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")

    for plan in Plan.objects.all():
        target = TARGETS.get(plan.tier)
        if target is None:
            # A tier this migration does not know about. Give it something
            # valid rather than leaving it unprovisionable, derived from its
            # own existing ceiling so no commercial intent is invented.
            ceiling = plan.max_storage_per_mailbox_mb
            plan.default_storage_per_mailbox_mb = min(BETA_MAILBOX_MB, ceiling)
            plan.max_storage_total_mb = ceiling * max(plan.max_mailboxes, 1)
            plan.save(update_fields=[
                "default_storage_per_mailbox_mb", "max_storage_total_mb",
            ])
            continue

        default_mb, max_mb, total_mb = target
        seeded = SEEDED_PER_MAILBOX_MB.get(plan.tier)

        if max_mb is not None:
            # Only raise the ceiling if the plan still holds its seeded value.
            if seeded is None or plan.max_storage_per_mailbox_mb == seeded:
                plan.max_storage_per_mailbox_mb = max_mb

        ceiling = plan.max_storage_per_mailbox_mb
        plan.default_storage_per_mailbox_mb = min(default_mb, ceiling)

        if total_mb is None:
            total_mb = ceiling * max(plan.max_mailboxes, 1)
        plan.max_storage_total_mb = max(total_mb, ceiling)

        plan.save(update_fields=[
            "default_storage_per_mailbox_mb",
            "max_storage_per_mailbox_mb",
            "max_storage_total_mb",
        ])


def unset_storage_totals(apps, schema_editor):
    """
    Restore the seeded per-mailbox ceilings. The two added columns are removed
    by the schema migration, so nothing else needs undoing.
    """
    Plan = apps.get_model("billing", "Plan")
    for tier, mb in SEEDED_PER_MAILBOX_MB.items():
        Plan.objects.filter(tier=tier).update(max_storage_per_mailbox_mb=mb)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0004_plan_default_storage_per_mailbox_mb_and_more"),
    ]

    operations = [
        migrations.RunPython(set_storage_totals, reverse_code=unset_storage_totals),
    ]
