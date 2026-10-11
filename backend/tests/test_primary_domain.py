"""Primary domain preference: one per organization, not mail routing or aliases."""
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.domains.models import Domain, DomainStatus
from apps.domains.primary import choose_initial_primary, set_primary_domain
from apps.tenants.models import MemberRole
from tests.factories import (
    add_member, auth_client, disable_throttling, make_domain,
    make_tenant, make_unverified_domain, make_user,
)


class PrimaryDomainTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("primary-owner@example.test")
        self.tenant = make_tenant(self.owner, slug="primary-main")
        self.client = auth_client(self.owner, self.tenant)
        self.first = make_domain(self.tenant, "first-primary.example")
        self.second = make_domain(self.tenant, "second-primary.example")
        self.other_owner = make_user("different-owner@example.test")
        self.other = make_tenant(self.other_owner, slug="primary-other")
        self.external = make_domain(self.other, "external-primary.example")

    def set_primary(self, domain, client=None):
        return (client or self.client).post(
            f"/api/domains/{domain.id}/set-primary/", {}, format="json",
        )

    def test_first_eligible_is_selected_deterministically(self):
        choose_initial_primary(self.tenant.id)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertTrue(self.first.is_primary)
        self.assertFalse(self.second.is_primary)
        choose_initial_primary(self.tenant.id)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_primary)

    def test_owner_changes_primary_and_list_exposes_one_marked_domain(self):
        self.assertEqual(self.set_primary(self.first).status_code, 200)
        response = self.set_primary(self.second)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["domain"]["is_primary"])
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertFalse(self.first.is_primary)
        self.assertTrue(self.second.is_primary)
        listing = self.client.get("/api/domains/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(
            [d["domain"] for d in listing.data if d["is_primary"]],
            ["second-primary.example"],
        )
        self.assertEqual(self.set_primary(self.second).status_code, 200)

    def test_unique_primary_is_enforced_by_database(self):
        self.set_primary(self.first)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Domain.objects.filter(pk=self.second.pk).update(is_primary=True)
        self.assertEqual(
            Domain.objects.filter(tenant=self.tenant, is_primary=True).count(), 1
        )

    def test_admin_allowed_support_and_read_only_forbidden(self):
        admin = make_user("admin-primary@example.test")
        support = make_user("support-primary@example.test")
        viewer = make_user("viewer-primary@example.test")
        add_member(self.tenant, admin, MemberRole.ADMIN)
        add_member(self.tenant, support, MemberRole.SUPPORT)
        add_member(self.tenant, viewer, MemberRole.READ_ONLY)
        self.assertEqual(self.set_primary(self.first, auth_client(admin, self.tenant)).status_code, 200)
        self.assertEqual(self.set_primary(self.second, auth_client(support, self.tenant)).status_code, 403)
        self.assertEqual(self.set_primary(self.second, auth_client(viewer, self.tenant)).status_code, 403)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_primary)

    def test_unverified_admin_rejected(self):
        user = make_user("unverified-primary@example.test", verified=False)
        add_member(self.tenant, user, MemberRole.ADMIN)
        self.assertEqual(self.set_primary(self.first, auth_client(user, self.tenant)).status_code, 403)
        self.assertFalse(Domain.objects.get(pk=self.first.pk).is_primary)

    def test_cross_tenant_target_hidden_and_unchanged(self):
        result = self.set_primary(self.external)
        self.assertEqual(result.status_code, 404)
        self.assertFalse(Domain.objects.get(pk=self.external.pk).is_primary)
        self.assertEqual(self.set_primary(self.first).status_code, 200)
        self.assertEqual(
            Domain.objects.filter(tenant=self.other, is_primary=True).count(), 0
        )

    def test_cannot_choose_unverified_or_dns_inactive_domain(self):
        unverified = make_unverified_domain(self.tenant, "unverified-primary.example")
        warning = make_domain(self.tenant, "warning-primary.example", status=DomainStatus.WARNING)
        self.assertEqual(self.set_primary(unverified).status_code, 409)
        self.assertEqual(self.set_primary(warning).status_code, 409)
        self.assertFalse(Domain.objects.filter(tenant=self.tenant, is_primary=True).exists())

    def test_temporary_dns_warning_does_not_switch_primary(self):
        self.set_primary(self.first)
        self.first.status = DomainStatus.WARNING
        self.first.save(update_fields=["status"])
        choose_initial_primary(self.tenant.id)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertTrue(self.first.is_primary)
        self.assertFalse(self.second.is_primary)

    def test_rotate_primary_verification_clears_and_selects_backup(self):
        self.set_primary(self.first)
        with mock.patch("apps.security.ratelimit.hit") as hit:
            hit.return_value.allowed = True
            result = self.client.post(
                f"/api/domains/{self.first.id}/rotate-verification-token/",
                {},
                format="json",
            )
        self.assertEqual(result.status_code, 200)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertFalse(self.first.is_primary)
        self.assertTrue(self.second.is_primary)

    def test_deleting_primary_selects_another_eligible_domain(self):
        self.set_primary(self.first)
        with mock.patch("apps.mail_engine.tasks.deprovision_domain_task.delay"):
            result = self.client.delete(f"/api/domains/{self.first.id}/")
        self.assertEqual(result.status_code, 204)
        self.assertFalse(Domain.objects.filter(pk=self.first.pk).exists())
        self.second.refresh_from_db()
        self.assertTrue(self.second.is_primary)

    def test_failed_delete_keeps_primary_untouched(self):
        self.set_primary(self.first)
        with mock.patch(
            "apps.mail_engine.tasks.deprovision_domain_task.delay",
            side_effect=OSError("synthetic broker failure"),
        ):
            result = self.client.delete(f"/api/domains/{self.first.id}/")
        self.assertEqual(result.status_code, 503)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_primary)
        self.assertTrue(Domain.objects.filter(pk=self.first.pk).exists())

    def test_initial_selection_is_tenant_scoped(self):
        choose_initial_primary(self.other.id)
        self.external.refresh_from_db()
        self.assertTrue(self.external.is_primary)
        self.assertFalse(Domain.objects.filter(tenant=self.tenant, is_primary=True).exists())
        domain, error = set_primary_domain(self.tenant.id, self.external.id)
        self.assertIsNone(domain)
        self.assertEqual(error, "missing")

    def test_existing_mailbox_address_is_unchanged_by_primary_preference(self):
        from tests.factories import make_mailbox
        mailbox = make_mailbox(self.tenant, self.first, local_part="contact")
        original = mailbox.email
        self.assertEqual(self.set_primary(self.second).status_code, 200)
        mailbox.refresh_from_db()
        self.assertEqual(mailbox.email, original)
        self.assertEqual(mailbox.domain_id, self.first.id)

    def test_only_active_verified_domains_are_candidates_for_initial_primary(self):
        self.first.status = DomainStatus.WARNING
        self.first.save(update_fields=["status"])
        self.second.status = DomainStatus.PENDING
        self.second.save(update_fields=["status"])
        choose_initial_primary(self.tenant.id)
        self.assertFalse(Domain.objects.filter(tenant=self.tenant, is_primary=True).exists())
        self.second.status = DomainStatus.ACTIVE
        self.second.save(update_fields=["status"])
        choose_initial_primary(self.tenant.id)
        self.second.refresh_from_db()
        self.assertTrue(self.second.is_primary)

    def test_verifying_first_dns_active_domain_auto_selects_primary(self):
        another_owner = make_user("new-primary-owner@example.test")
        tenant = make_tenant(another_owner, slug="first-new-primary")
        domain = make_unverified_domain(tenant, "first-new-primary.example", status=DomainStatus.ACTIVE)
        client = auth_client(another_owner, tenant)
        with (
            mock.patch("apps.domains.views._enforce_domain_check_limit"),
            mock.patch(
                "apps.domains.verification.check_ownership_dns",
                return_value=(True, "", ""),
            ),
            mock.patch("apps.mail_engine.tasks.provision_domain_task.delay"),
        ):
            result = client.post(
                f"/api/domains/{domain.id}/verify-ownership/", {}, format="json"
            )
        self.assertEqual(result.status_code, 200)
        domain.refresh_from_db()
        self.assertTrue(domain.is_primary)
        self.assertTrue(result.data["domain"]["is_primary"])

    def test_backfill_is_idempotent_and_chooses_oldest_eligible(self):
        from importlib import import_module
        from django.apps import apps

        migration = import_module("apps.domains.migrations.0007_primary_domain")
        migration.backfill_primary(apps, None)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_primary)
        migration.backfill_primary(apps, None)
        self.assertEqual(
            Domain.objects.filter(tenant=self.tenant, is_primary=True).count(), 1
        )

    def test_existing_data_backfill_and_unique_index_use_separate_transactions(self):
        """Regression: production PG rejects CREATE INDEX after uncommitted
        data updates on domains_domain with pending trigger events."""
        from importlib import import_module
        from django.db.migrations.operations.models import AddConstraint
        from django.db.migrations.operations.special import RunPython

        populate = import_module("apps.domains.migrations.0007_primary_domain").Migration
        unique = import_module("apps.domains.migrations.0008_unique_primary_domain").Migration
        self.assertTrue(any(isinstance(op, RunPython) for op in populate.operations))
        self.assertFalse(any(isinstance(op, AddConstraint) for op in populate.operations))
        self.assertEqual(unique.dependencies, [("domains", "0007_primary_domain")])
        self.assertEqual(len(unique.operations), 1)
        self.assertIsInstance(unique.operations[0], AddConstraint)
        self.assertEqual(
            unique.operations[0].constraint.name, "uniq_primary_domain_per_tenant"
        )
