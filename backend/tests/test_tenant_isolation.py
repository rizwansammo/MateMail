"""
Tenant isolation tests.

These tests verify the core invariant: a tenant can never read, write, or
enumerate another tenant's data. Every test that crosses a tenant boundary
must either return an empty queryset or raise an exception — never return
the other tenant's data.

Run with:
    python manage.py test tests.test_tenant_isolation --settings=config.settings.dev
"""
from django.test import TestCase

from apps.accounts.models import User
from apps.aliases.models import Alias
from apps.domains.models import Domain
from apps.logs.models import MailLog
from apps.mailboxes.models import Mailbox
from apps.tenants.managers import TenantScopedManager
from apps.tenants.models import Tenant, TenantMembership, MemberRole


def make_user(email, **kwargs):
    return User.objects.create_user(email=email, password="testpass123!", **kwargs)


def make_tenant(owner, name, slug):
    return Tenant.objects.create(name=name, slug=slug, owner=owner, status="active")


def make_domain(tenant, domain_name):
    return Domain.objects.create(tenant=tenant, domain=domain_name)


def make_mailbox(tenant, domain, local_part, full_name="Test User"):
    return Mailbox.objects.create(
        tenant=tenant,
        domain=domain,
        local_part=local_part,
        full_name=full_name,
        email=f"{local_part}@{domain.domain}",
    )


class TenantIsolationSetup(TestCase):
    def setUp(self):
        # Tenant A
        self.owner_a = make_user("owner-a@tenant-a.com")
        self.tenant_a = make_tenant(self.owner_a, "Tenant A", "tenant-a")
        TenantMembership.objects.create(
            tenant=self.tenant_a, user=self.owner_a, role=MemberRole.OWNER, status="active"
        )
        self.domain_a = make_domain(self.tenant_a, "tenant-a.example.com")
        self.mailbox_a = make_mailbox(self.tenant_a, self.domain_a, "alice")

        # Tenant B
        self.owner_b = make_user("owner-b@tenant-b.com")
        self.tenant_b = make_tenant(self.owner_b, "Tenant B", "tenant-b")
        TenantMembership.objects.create(
            tenant=self.tenant_b, user=self.owner_b, role=MemberRole.OWNER, status="active"
        )
        self.domain_b = make_domain(self.tenant_b, "tenant-b.example.com")
        self.mailbox_b = make_mailbox(self.tenant_b, self.domain_b, "bob")


class DomainIsolationTest(TenantIsolationSetup):
    def test_for_tenant_only_returns_own_domains(self):
        domains_a = Domain.objects.for_tenant(self.tenant_a)
        self.assertEqual(domains_a.count(), 1)
        self.assertEqual(domains_a.first().domain, "tenant-a.example.com")

    def test_for_tenant_does_not_return_other_tenant_domains(self):
        domains_a = Domain.objects.for_tenant(self.tenant_a)
        domain_names = list(domains_a.values_list("domain", flat=True))
        self.assertNotIn("tenant-b.example.com", domain_names)

    def test_cross_tenant_domain_access_via_pk_is_not_in_scoped_qs(self):
        """
        Even if an attacker knows domain_b's primary key, they cannot fetch
        it through tenant_a's scoped queryset.
        """
        result = Domain.objects.for_tenant(self.tenant_a).filter(pk=self.domain_b.pk)
        self.assertEqual(result.count(), 0)

    def test_total_domain_count_not_leaked(self):
        """for_tenant() must not expose count of all domains."""
        self.assertEqual(Domain.objects.for_tenant(self.tenant_a).count(), 1)
        self.assertEqual(Domain.objects.for_tenant(self.tenant_b).count(), 1)
        self.assertEqual(Domain.objects.count(), 2)  # total, not scoped


class MailboxIsolationTest(TenantIsolationSetup):
    def test_for_tenant_only_returns_own_mailboxes(self):
        mbs_a = Mailbox.objects.for_tenant(self.tenant_a)
        self.assertEqual(mbs_a.count(), 1)
        self.assertEqual(mbs_a.first().email, "alice@tenant-a.example.com")

    def test_cross_tenant_mailbox_not_accessible(self):
        result = Mailbox.objects.for_tenant(self.tenant_a).filter(pk=self.mailbox_b.pk)
        self.assertEqual(result.count(), 0)

    def test_duplicate_local_part_same_domain_raises(self):
        """Two mailboxes with the same local_part on the same domain must be rejected."""
        with self.assertRaises(Exception):
            Mailbox.objects.create(
                tenant=self.tenant_a,
                domain=self.domain_a,
                local_part="alice",   # already exists as alice@tenant-a.example.com
                full_name="Alice Duplicate",
            )

    def test_same_local_part_different_domain_is_allowed(self):
        """alice@tenant-b.example.com is a different address from alice@tenant-a.example.com."""
        mb = Mailbox.objects.create(
            tenant=self.tenant_b,
            domain=self.domain_b,
            local_part="alice",
            full_name="Alice B",
        )
        self.assertEqual(mb.email, "alice@tenant-b.example.com")


class AliasIsolationTest(TenantIsolationSetup):
    def setUp(self):
        super().setUp()
        self.alias_a = Alias.objects.create(
            tenant=self.tenant_a,
            domain=self.domain_a,
            source_address="hello@tenant-a.example.com",
            destination_mailbox=self.mailbox_a,
        )

    def test_for_tenant_only_returns_own_aliases(self):
        aliases = Alias.objects.for_tenant(self.tenant_a)
        self.assertEqual(aliases.count(), 1)

    def test_cross_tenant_alias_not_visible(self):
        aliases_b = Alias.objects.for_tenant(self.tenant_b)
        self.assertEqual(aliases_b.count(), 0)


class LogIsolationTest(TenantIsolationSetup):
    def setUp(self):
        super().setUp()
        MailLog.objects.create(
            tenant=self.tenant_a,
            event_type="mailbox_created",
            source="owner-a@tenant-a.com",
            result="success",
        )
        MailLog.objects.create(
            tenant=self.tenant_b,
            event_type="mailbox_created",
            source="owner-b@tenant-b.com",
            result="success",
        )

    def test_for_tenant_only_returns_own_logs(self):
        logs_a = MailLog.objects.for_tenant(self.tenant_a)
        self.assertEqual(logs_a.count(), 1)
        self.assertEqual(logs_a.first().source, "owner-a@tenant-a.com")

    def test_cross_tenant_log_not_visible(self):
        logs_a = MailLog.objects.for_tenant(self.tenant_a)
        sources = list(logs_a.values_list("source", flat=True))
        self.assertNotIn("owner-b@tenant-b.com", sources)


class TenantScopedManagerTest(TenantIsolationSetup):
    def test_manager_type(self):
        self.assertIsInstance(Domain.objects, TenantScopedManager)
        self.assertIsInstance(Mailbox.objects, TenantScopedManager)
        self.assertIsInstance(Alias.objects, TenantScopedManager)
        self.assertIsInstance(MailLog.objects, TenantScopedManager)

    def test_for_tenant_returns_queryset(self):
        from django.db.models import QuerySet
        qs = Domain.objects.for_tenant(self.tenant_a)
        self.assertIsInstance(qs, QuerySet)

    def test_tenant_cannot_see_other_tenants_membership(self):
        memberships_a = TenantMembership.objects.filter(tenant=self.tenant_a)
        user_ids = list(memberships_a.values_list("user_id", flat=True))
        self.assertNotIn(self.owner_b.id, user_ids)
