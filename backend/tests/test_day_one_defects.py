"""
Regressions for the confirmed day-one defects fixed in Phase 0.

Each of these was reproducible against the pre-Phase-0 tree.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.domains.models import Domain
from apps.domains.serializers import DomainCreateSerializer
from apps.logs.models import LogEventType, MailLog
from apps.logs.utils import log_event
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "day-one-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class AuditLogWritesTest(TestCase):
    """
    The Phase 10 model rewrite dropped MailLog.Meta.db_table, repointing the ORM
    at a table that migration 0001 never created. Because log_event() swallows
    exceptions, every audit write failed silently — the log looked quiet rather
    than broken.
    """

    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("audit@example.test")
        self.tenant = make_tenant(self.user, name="Audit Co", slug="audit-co")

    def test_maillog_table_is_writable_and_readable(self):
        entry = MailLog.objects.create(
            tenant=self.tenant, event_type=LogEventType.DOMAIN_ADDED, result="success"
        )
        self.assertTrue(MailLog.objects.filter(pk=entry.pk).exists())

    def test_model_and_migration_agree_on_the_table_name(self):
        self.assertEqual(MailLog._meta.db_table, "logs_mail_log")

    def test_log_event_actually_persists(self):
        before = MailLog.objects.count()
        log_event(self.tenant, LogEventType.DOMAIN_ADDED, result="success")
        self.assertEqual(
            MailLog.objects.count(), before + 1,
            "log_event() did not persist an audit record",
        )

    def test_domain_creation_writes_an_audit_record(self):
        """End to end through the API — the path that was silently dropping events."""
        before = MailLog.objects.filter(tenant=self.tenant).count()
        res = auth_client(self.user, self.tenant).post(
            "/api/domains/", {"domain": "audited.example"}, format="json"
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(
            MailLog.objects.filter(tenant=self.tenant).count(), before + 1
        )

    def test_audit_log_api_returns_records(self):
        log_event(self.tenant, LogEventType.DOMAIN_ADDED, result="success")
        res = auth_client(self.user, self.tenant).get("/api/logs/")
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(len(res.data), 1)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PlatformAdminTenantDetailTest(TestCase):
    """
    AdminTenantDetailView queried Domain fields `name` and `created_at`, which do
    not exist (they are `domain` and `added_at`), so the endpoint raised
    FieldError and the whole /admin/tenants/[id] page was dead.
    """

    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.staff = User.objects.create_superuser(
            email="platform@example.test", password=TEST_PASSWORD, full_name="Platform"
        )
        self.owner = make_user("tenant-owner@example.test")
        self.tenant = make_tenant(self.owner, name="Inspected", slug="inspected")
        make_domain(self.tenant, "inspected.example")

    def test_tenant_detail_returns_200(self):
        res = auth_client(self.staff).get(f"/api/platform/tenants/{self.tenant.id}/")
        self.assertEqual(res.status_code, 200)

    def test_tenant_detail_keeps_the_frontend_contract(self):
        res = auth_client(self.staff).get(f"/api/platform/tenants/{self.tenant.id}/")
        self.assertEqual(len(res.data["domains"]), 1)
        entry = res.data["domains"][0]
        # The admin UI reads name/created_at; the values come from domain/added_at.
        for key in ("id", "name", "status", "created_at"):
            self.assertIn(key, entry)
        self.assertEqual(entry["name"], "inspected.example")
        self.assertIsNotNone(entry["created_at"])

    def test_tenant_detail_includes_recent_logs(self):
        """Also exercises the MailLog query in the same view."""
        res = auth_client(self.staff).get(f"/api/platform/tenants/{self.tenant.id}/")
        self.assertIn("recent_logs", res.data)

    def test_tenant_list_and_stats_still_work(self):
        client = auth_client(self.staff)
        self.assertEqual(client.get("/api/platform/stats/").status_code, 200)
        self.assertEqual(client.get("/api/platform/tenants/").status_code, 200)


class DomainNormalizationTest(TestCase):
    """
    The old validator used str.lstrip("www.") and str.lstrip("http://"), which
    strip a *character set* rather than a prefix. "web.example.com" became
    "eb.example.com", silently corrupting the domain a customer typed.
    """

    def normalize(self, value):
        serializer = DomainCreateSerializer(data={"domain": value})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["domain"]

    def test_prefixes_are_stripped_correctly(self):
        cases = {
            "example.com": "example.com",
            "www.example.com": "example.com",
            "http://example.com": "example.com",
            "https://example.com": "example.com",
            "https://www.example.com": "example.com",
            "  Example.COM  ": "example.com",
            "example.com.": "example.com",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.normalize(raw), expected)

    def test_leading_letters_of_the_real_label_survive(self):
        """The actual lstrip bug: these must not lose their leading characters."""
        cases = {
            "web.example.com": "web.example.com",
            "wp.example.com": "wp.example.com",
            "w.example.com": "w.example.com",
            "hot.example.com": "hot.example.com",
            "ptt.example.com": "ptt.example.com",
            "throw.example.com": "throw.example.com",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(self.normalize(raw), expected)

    def test_invalid_hostnames_are_rejected(self):
        from rest_framework.exceptions import ValidationError

        for raw in [
            "nodots",
            "example",
            "-bad.example.com",
            "bad-.example.com",
            "spaces in.example.com",
            "example.c",
            "example.123",
            "under_score.example.com",
            "semi;colon.example.com",
            "",
        ]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    self.normalize(raw)

    def test_duplicate_domain_is_rejected(self):
        from rest_framework.exceptions import ValidationError

        owner = make_user("dup@example.test")
        tenant = make_tenant(owner, name="Dup", slug="dup")
        Domain.objects.create(tenant=tenant, domain="taken.example")
        with self.assertRaises(ValidationError):
            self.normalize("https://www.taken.example")


class BillingUtilsImportTest(TestCase):
    """
    days_left_on_trial() imported `billing.models` instead of
    `apps.billing.models`, which raises ModuleNotFoundError.

    The bad import sits *after* an early return, so a tenant with no
    subscription never reaches it. These tests deliberately attach a trialing
    subscription so the import line actually executes.
    """

    def setUp(self):
        from django.utils import timezone

        from apps.billing.models import Plan, PlanTier, Subscription, SubscriptionStatus

        self.owner = make_user("trial@example.test")
        self.tenant = make_tenant(self.owner, name="Trial Co", slug="trial-co")
        # Plans are seeded by migration 0003; fall back if the tier is missing.
        plan = Plan.objects.filter(tier=PlanTier.TRIAL).first() or Plan.objects.create(
            tier=PlanTier.TRIAL,
            display_name="Trial",
            max_domains=1,
            max_mailboxes=3,
            max_storage_per_mailbox_mb=1024,
        )
        self.subscription = Subscription.objects.create(
            tenant=self.tenant,
            plan=plan,
            status=SubscriptionStatus.TRIALING,
            trial_ends_at=timezone.now() + timezone.timedelta(days=10),
        )

    def test_days_left_on_trial_executes_the_import_path(self):
        from apps.billing.utils import days_left_on_trial

        # Reaches the previously-broken `from billing.models import ...` line.
        self.assertEqual(days_left_on_trial(self.tenant), 9)

    def test_days_left_is_zero_once_not_trialing(self):
        from apps.billing.models import SubscriptionStatus
        from apps.billing.utils import days_left_on_trial

        self.subscription.status = SubscriptionStatus.ACTIVE
        self.subscription.save(update_fields=["status"])
        self.assertEqual(days_left_on_trial(self.tenant), 0)

    def test_days_left_is_zero_without_a_subscription(self):
        from apps.billing.utils import days_left_on_trial

        other = make_user("nosub@example.test")
        tenant = make_tenant(other, name="No Sub", slug="no-sub")
        self.assertEqual(days_left_on_trial(tenant), 0)
