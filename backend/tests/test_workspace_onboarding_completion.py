"""Permanent, tenant-isolated Getting Started lifecycle regression tests."""
from importlib import import_module

from django.apps import apps as django_apps
from django.db import connection
from django.test import TestCase, override_settings

from apps.domains.models import DomainOwnership, DomainStatus
from apps.tenants.models import Tenant, TenantMembership, MemberRole, MemberStatus
from tests.factories import (
    FAST_PASSWORD_HASHERS, auth_client, make_domain, make_mailbox,
    make_tenant, make_user,
)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class OnboardingCompletionTest(TestCase):
    def setUp(self):
        self.owner = make_user("onboard-owner@example.com")
        self.tenant = make_tenant(
            self.owner, name="Onboarding LLC", slug="onboarding-llc"
        )
        self.client = auth_client(self.owner, self.tenant)
        self.url = f"/api/workspaces/{self.tenant.pk}/onboarding/"

    def status(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_new_tenant_needs_initial_setup(self):
        state = self.status()
        self.assertTrue(state["workspace_created"])
        self.assertFalse(state["domain_added"])
        self.assertFalse(state["dns_verified"])
        self.assertFalse(state["first_mailbox_created"])
        self.assertFalse(state["completed"])
        self.assertIsNone(state["completed_at"])
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.onboarding_completed_at)

    def test_domain_must_be_owned_and_dns_ready(self):
        domain = make_domain(
            self.tenant, "unverified-onboarding.example", verified=False,
            status=DomainStatus.ACTIVE,
        )
        mailbox = make_mailbox(self.tenant, domain, "first")
        state = self.status()
        self.assertTrue(state["domain_added"])
        self.assertTrue(state["first_mailbox_created"])
        self.assertFalse(state["dns_verified"])
        self.assertFalse(state["completed"])

        domain.ownership_status = DomainOwnership.VERIFIED
        domain.status = DomainStatus.WARNING
        domain.save(update_fields=["ownership_status", "status"])
        self.assertFalse(self.status()["completed"])

        domain.status = DomainStatus.ACTIVE
        domain.save(update_fields=["status"])
        self.assertTrue(self.status()["completed"])
        mailbox.refresh_from_db()

    def test_creation_stamps_completion_immediately_and_idempotently(self):
        domain = make_domain(self.tenant, "ready-onboarding.example")
        self.assertFalse(self.status()["completed"])
        make_mailbox(self.tenant, domain, "first")
        self.tenant.refresh_from_db()
        self.assertIsNotNone(self.tenant.onboarding_completed_at)
        completed_at = self.tenant.onboarding_completed_at
        response = self.status()
        self.assertTrue(response["completed"])
        self.assertIsNotNone(response["completed_at"])
        self.status()
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.onboarding_completed_at, completed_at)

    def test_get_repairs_updates_that_bypass_post_save_signals(self):
        domain = make_domain(
            self.tenant, "bulk-onboarding.example",
            status=DomainStatus.PENDING, verified=True,
        )
        make_mailbox(self.tenant, domain, "first")
        self.assertFalse(self.status()["completed"])
        type(domain).objects.filter(pk=domain.pk).update(status=DomainStatus.ACTIVE)
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.onboarding_completed_at)
        self.assertTrue(self.status()["completed"])
        self.tenant.refresh_from_db()
        self.assertIsNotNone(self.tenant.onboarding_completed_at)

    def test_completed_remains_complete_after_dns_failure_or_domain_deletion(self):
        domain = make_domain(self.tenant, "sticky-onboarding.example")
        make_mailbox(self.tenant, domain, "first")
        baseline = self.status()["completed_at"]
        self.assertTrue(baseline)

        domain.status = DomainStatus.FAILED
        domain.save(update_fields=["status"])
        state = self.status()
        self.assertFalse(state["dns_verified"])
        self.assertTrue(state["completed"])
        self.assertEqual(state["completed_at"], baseline)

        domain.delete()
        state = self.status()
        self.assertFalse(state["domain_added"])
        self.assertFalse(state["first_mailbox_created"])
        self.assertTrue(state["completed"])
        self.assertEqual(state["completed_at"], baseline)

    def test_completion_is_same_for_separate_browsers_and_members(self):
        domain = make_domain(self.tenant, "devices-onboarding.example")
        make_mailbox(self.tenant, domain, "first")
        other_user = make_user("admin-onboarding@example.com")
        TenantMembership.objects.create(
            tenant=self.tenant, user=other_user,
            role=MemberRole.ADMIN, status=MemberStatus.ACTIVE,
        )
        other_browser = auth_client(other_user, self.tenant)
        response = other_browser.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["completed"])
        self.assertEqual(response.data["completed_at"], self.status()["completed_at"])

    def test_foreign_tenant_and_nonmember_cannot_query_onboarding(self):
        owner_b = make_user("owner-b-onboarding@example.com")
        tenant_b = make_tenant(owner_b, name="Foreign", slug="foreign-onboarding")
        foreign_url = f"/api/workspaces/{tenant_b.pk}/onboarding/"
        response = self.client.get(foreign_url)
        self.assertEqual(response.status_code, 404)
        response = auth_client(owner_b, tenant_b).get(self.url)
        self.assertEqual(response.status_code, 404)
        tenant_b.refresh_from_db()
        self.assertIsNone(tenant_b.onboarding_completed_at)

    def test_migration_backfill_preserves_existing_completed_organizations(self):
        ready_domain = make_domain(self.tenant, "legacy-complete.example")
        make_mailbox(self.tenant, ready_domain, "first")
        Tenant.objects.filter(pk=self.tenant.pk).update(onboarding_completed_at=None)

        owner_b = make_user("legacy-partial@example.com")
        tenant_b = make_tenant(owner_b, name="Legacy Partial", slug="legacy-partial")
        make_domain(tenant_b, "legacy-partial.example", verified=False)

        backfill = import_module(
            "apps.tenants.migrations.0005_onboarding_completed_at"
        ).mark_existing_completed

        class SchemaEditor:
            connection = connection

        backfill(django_apps, SchemaEditor())
        self.tenant.refresh_from_db()
        tenant_b.refresh_from_db()
        self.assertIsNotNone(self.tenant.onboarding_completed_at)
        self.assertIsNone(tenant_b.onboarding_completed_at)
