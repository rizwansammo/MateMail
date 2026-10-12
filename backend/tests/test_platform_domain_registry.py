"""Phase 2 global domain ownership protection.

These tests do not touch MateServer, the public DNS zone, or the mail engine.
"""
from unittest import mock

from django.test import TestCase

from apps.domains.models import (
    Domain, DomainOwnership, MailDomainOwnership, MailDomainOwnerKind,
)
from apps.domains.ownership_registry import (
    DomainRegistryConflict, assert_registry_authorizes_domain,
    claim_verified_domain, ownership_for,
)
from apps.domains.serializers import DomainCreateSerializer
from apps.domains.verification import (
    DomainNotVerified, assert_provisionable, verify_domain_ownership,
)
from tests.factories import make_domain, make_tenant, make_unverified_domain, make_user


class PlatformDomainRegistryTests(TestCase):
    def setUp(self):
        self.owner = make_user("registry-owner@example.test")
        self.tenant = make_tenant(self.owner, name="Registry", slug="registry")

    def test_legacy_and_future_platform_identity_are_reserved_without_engine_mutation(self):
        for name in ("mail.matemail.pro", "system.matemail.pro"):
            claim = ownership_for(name)
            self.assertIsNotNone(claim)
            self.assertEqual(claim.owner_kind, MailDomainOwnerKind.PLATFORM)
            self.assertIsNone(claim.tenant_id)
            self.assertFalse(Domain.objects.filter(domain=name).exists())

    def test_hub_cannot_claim_a_platform_domain(self):
        for name in ("mail.matemail.pro", "system.matemail.pro"):
            serializer = DomainCreateSerializer(data={"domain": name})
            self.assertFalse(serializer.is_valid())
            self.assertIn("domain", serializer.errors)

    def test_verified_customer_domains_have_a_matching_registry_entry(self):
        customer = make_domain(self.tenant, "registry-client.example")
        claim = assert_registry_authorizes_domain(customer)
        self.assertEqual(claim.source_domain_id, customer.pk)
        self.assertEqual(claim.tenant_id, self.tenant.pk)
        assert_provisionable(customer)

    def test_pending_claim_only_acquires_registry_after_dns_verification(self):
        customer = make_unverified_domain(self.tenant, "newregistry.example")
        self.assertIsNone(ownership_for(customer.domain))
        from apps.domains.verification import generate_verification_token
        with mock.patch(
            "apps.domains.verification.check_ownership_dns",
            return_value=(True, "Verified.", ""),
        ):
            ok, _ = verify_domain_ownership(customer)
        self.assertTrue(ok)
        self.assertEqual(ownership_for(customer.domain).source_domain_id, customer.pk)

    def test_cannot_transfer_claim_to_other_organization_or_domain_row(self):
        first = make_domain(self.tenant, "already-claimed.example")
        other = make_tenant(
            make_user("registry-other@example.test"),
            name="Other", slug="registry-other",
        )
        pending = make_unverified_domain(other, first.domain)
        with self.assertRaises(DomainRegistryConflict):
            claim_verified_domain(pending)
        with self.assertRaises(DomainRegistryConflict):
            claim_verified_domain(make_unverified_domain(self.tenant, first.domain))

    def test_registry_refuses_provisioning_when_claim_missing(self):
        pending = make_unverified_domain(self.tenant, "registry-missing.example")
        Domain.objects.filter(pk=pending.pk).update(ownership_status=DomainOwnership.VERIFIED)
        pending.refresh_from_db()
        with self.assertRaises(DomainNotVerified):
            assert_provisionable(pending)

    def test_django_domain_delete_does_not_release_ownership_before_engine_cleanup(self):
        customer = make_domain(self.tenant, "cleanup-pending.example")
        customer.delete()
        claim = ownership_for("cleanup-pending.example")
        self.assertIsNotNone(claim)
        self.assertEqual(claim.source_domain_id, customer.pk)
