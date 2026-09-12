"""
The P5 data migrations.

Migrations are run once, in production, usually at a moment when nobody wants
surprises. These tests drive the migration functions directly against the real
model registry, so their behaviour is pinned before that happens rather than
discovered afterwards.

Production held zero tenants, zero domains, zero mailboxes and four unmodified
plans when these were written — verified against the live database, not assumed.
That makes the tenant backfill a no-op there; it exists for development and
staging databases and so the schema change is safe to apply anywhere.
"""
import importlib

from django.apps import apps as global_apps
from django.test import TestCase
from django.utils import timezone

from apps.billing.models import Plan
from apps.tenants.models import Tenant, TenantStatus
from tests.factories import make_tenant, make_user

backfill = importlib.import_module("apps.tenants.migrations.0003_backfill_approval")
seed_limits = importlib.import_module("apps.billing.migrations.0007_seed_sending_limits")


class TenantApprovalBackfillTest(TestCase):
    """
    P5 makes `approved_at` a requirement for mail access, and the new column
    arrives NULL. Without a backfill the schema change would silently revoke
    Mail Engine access from every workspace that already had it — a policy
    change applied retroactively as a side effect.
    """

    def setUp(self):
        self.user = make_user("owner@legacy.example")

    def _legacy(self, status, slug):
        """A workspace as it looked before the approval gate existed."""
        tenant = make_tenant(self.user, slug=slug, status=status, approved=False)
        Tenant.objects.filter(pk=tenant.pk).update(approved_at=None)
        return tenant

    def test_an_active_workspace_keeps_its_mail_access(self):
        tenant = self._legacy(TenantStatus.ACTIVE, "legacy-active")
        self.assertFalse(tenant.can_use_mail)

        backfill.grandfather_existing(global_apps, None)

        tenant.refresh_from_db()
        self.assertIsNotNone(tenant.approved_at)
        self.assertTrue(tenant.can_use_mail)

    def test_a_trial_workspace_keeps_its_mail_access(self):
        tenant = self._legacy(TenantStatus.TRIAL, "legacy-trial")
        backfill.grandfather_existing(global_apps, None)
        tenant.refresh_from_db()
        self.assertTrue(tenant.can_use_mail)

    def test_no_approver_is_invented(self):
        """
        No human approved these, and recording one would corrupt the audit
        trail. The reason text says so instead.
        """
        tenant = self._legacy(TenantStatus.ACTIVE, "legacy-active")
        backfill.grandfather_existing(global_apps, None)
        tenant.refresh_from_db()
        self.assertIsNone(tenant.approved_by)
        self.assertIn("before platform approval", tenant.review_reason)

    def test_workspaces_without_mail_access_gain_none(self):
        for status in (
            TenantStatus.PENDING_APPROVAL,
            TenantStatus.SUSPENDED,
            TenantStatus.CANCELLED,
            TenantStatus.REJECTED,
        ):
            with self.subTest(status=status):
                tenant = self._legacy(status, f"legacy-{status}")
                backfill.grandfather_existing(global_apps, None)
                tenant.refresh_from_db()
                self.assertIsNone(tenant.approved_at)
                self.assertFalse(tenant.can_use_mail)

    def test_a_real_approval_is_not_overwritten(self):
        tenant = make_tenant(self.user, slug="really-approved", status=TenantStatus.ACTIVE)
        approved_at = tenant.approved_at
        backfill.grandfather_existing(global_apps, None)
        tenant.refresh_from_db()
        self.assertEqual(tenant.approved_at, approved_at)
        self.assertEqual(tenant.review_reason, "")

    def test_the_reverse_leaves_genuine_approvals_alone(self):
        """
        A rollback must strip only what the migration itself wrote, not an
        approval a real admin granted afterwards.
        """
        legacy = self._legacy(TenantStatus.ACTIVE, "legacy-active")
        genuine = make_tenant(self.user, slug="genuine", status=TenantStatus.ACTIVE)

        backfill.grandfather_existing(global_apps, None)
        backfill.ungrandfather(global_apps, None)

        legacy.refresh_from_db()
        genuine.refresh_from_db()
        self.assertIsNone(legacy.approved_at)
        self.assertIsNotNone(genuine.approved_at)

    def test_it_is_safe_to_run_twice(self):
        tenant = self._legacy(TenantStatus.ACTIVE, "legacy-active")
        backfill.grandfather_existing(global_apps, None)
        tenant.refresh_from_db()
        first = tenant.approved_at

        backfill.grandfather_existing(global_apps, None)
        tenant.refresh_from_db()
        self.assertEqual(tenant.approved_at, first)


class SendingLimitSeedTest(TestCase):
    """
    Per-tier alias and sending limits, replacing three literals that were
    identical for a free beta workspace and a future enterprise plan.
    """

    def _reset_to_field_defaults(self):
        Plan.objects.all().update(**seed_limits.FIELD_DEFAULTS)

    def test_each_tier_gets_its_intended_limits(self):
        self._reset_to_field_defaults()
        seed_limits.set_sending_limits(global_apps, None)

        for tier, (aliases, per_hour, per_day) in seed_limits.TARGETS.items():
            plan = Plan.objects.filter(tier=tier).first()
            if plan is None:
                continue
            with self.subTest(tier=tier):
                self.assertEqual(plan.max_aliases, aliases)
                self.assertEqual(plan.max_messages_per_hour_per_mailbox, per_hour)
                self.assertEqual(plan.max_messages_per_day_per_tenant, per_day)

    def test_limits_rise_with_the_tier(self):
        self._reset_to_field_defaults()
        seed_limits.set_sending_limits(global_apps, None)

        order = ["trial", "starter", "business", "infrastructure"]
        plans = [Plan.objects.filter(tier=t).first() for t in order]
        plans = [p for p in plans if p is not None]

        hourly = [p.max_messages_per_hour_per_mailbox for p in plans]
        daily = [p.max_messages_per_day_per_tenant for p in plans]
        self.assertEqual(hourly, sorted(hourly))
        self.assertEqual(daily, sorted(daily))

    def test_an_operator_customised_plan_is_left_alone(self):
        """
        Overwriting a deliberate commercial limit is worse than leaving one
        plan needing manual attention.
        """
        self._reset_to_field_defaults()
        Plan.objects.filter(tier="business").update(
            max_messages_per_hour_per_mailbox=42
        )

        seed_limits.set_sending_limits(global_apps, None)

        business = Plan.objects.get(tier="business")
        self.assertEqual(business.max_messages_per_hour_per_mailbox, 42)
        # The fields it had NOT customised are still seeded.
        self.assertEqual(
            business.max_messages_per_day_per_tenant,
            seed_limits.TARGETS["business"][2],
        )

    def test_an_unknown_tier_keeps_the_tightest_values(self):
        """
        Guessing generous limits for a plan nobody has classified is how a
        sending reputation gets spent.
        """
        self._reset_to_field_defaults()
        Plan.objects.create(
            tier="some-future-tier",
            display_name="Future",
            max_domains=1,
            max_mailboxes=1,
            max_storage_per_mailbox_mb=1024,
        )

        seed_limits.set_sending_limits(global_apps, None)

        plan = Plan.objects.get(tier="some-future-tier")
        self.assertEqual(
            plan.max_messages_per_hour_per_mailbox,
            seed_limits.FIELD_DEFAULTS["max_messages_per_hour_per_mailbox"],
        )

    def test_it_is_safe_to_run_twice(self):
        self._reset_to_field_defaults()
        seed_limits.set_sending_limits(global_apps, None)
        before = list(
            Plan.objects.order_by("tier").values_list(
                "tier", "max_aliases", "max_messages_per_hour_per_mailbox"
            )
        )
        seed_limits.set_sending_limits(global_apps, None)
        after = list(
            Plan.objects.order_by("tier").values_list(
                "tier", "max_aliases", "max_messages_per_hour_per_mailbox"
            )
        )
        self.assertEqual(before, after)

    def test_the_reverse_restores_the_field_defaults(self):
        seed_limits.set_sending_limits(global_apps, None)
        seed_limits.unset_sending_limits(global_apps, None)

        for tier in seed_limits.TARGETS:
            plan = Plan.objects.filter(tier=tier).first()
            if plan is None:
                continue
            with self.subTest(tier=tier):
                self.assertEqual(
                    plan.max_aliases, seed_limits.FIELD_DEFAULTS["max_aliases"]
                )


class MigratedStateMatchesTheModelsTest(TestCase):
    """
    The migrated database is what the test database already is, so this pins
    the end state rather than the steps.
    """

    def test_every_seeded_plan_is_internally_consistent(self):
        """
        The Mail Engine refuses a domain whose storage numbers contradict each
        other, and it enforces all three at once.
        """
        for plan in Plan.objects.all():
            with self.subTest(tier=plan.tier):
                self.assertLessEqual(
                    plan.default_storage_per_mailbox_mb,
                    plan.max_storage_per_mailbox_mb,
                )
                self.assertLessEqual(
                    plan.max_storage_per_mailbox_mb, plan.max_storage_total_mb
                )

    def test_a_new_tenant_defaults_to_pending_approval(self):
        user = make_user("fresh@example.test")
        tenant = Tenant.objects.create(name="Fresh", slug="fresh", owner=user)
        self.assertEqual(tenant.status, TenantStatus.PENDING_APPROVAL)
        self.assertIsNone(tenant.approved_at)

    def test_the_status_column_fits_the_longest_status(self):
        longest = max(TenantStatus.values, key=len)
        field = Tenant._meta.get_field("status")
        self.assertGreaterEqual(field.max_length, len(longest))

    def test_a_domain_starts_with_no_recheck_failures(self):
        from apps.domains.models import Domain

        user = make_user("dom@example.test")
        tenant = make_tenant(user, slug="dom")
        domain = Domain.objects.create(tenant=tenant, domain="fresh.example")
        self.assertEqual(domain.ownership_recheck_failures, 0)

    def test_timestamps_are_timezone_aware(self):
        user = make_user("tz@example.test")
        tenant = make_tenant(user, slug="tz", status=TenantStatus.ACTIVE)
        self.assertIsNotNone(timezone.is_aware(tenant.approved_at))
