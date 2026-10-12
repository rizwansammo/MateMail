"""Phase 3 Platform system mail configuration security and immutability."""
from django.db import DatabaseError, transaction
from unittest import mock
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import MailDomainOwnership, MailDomainOwnerKind
from apps.platform_admin.models import (
    PlatformAuditLog, PlatformSystemMailPointer, PlatformSystemMailRevision,
)
from apps.platform_admin.system_mail import DEFAULT_PROPOSAL
from tests.factories import (
    FAST_PASSWORD_HASHERS, auth_client, make_tenant, make_user,
    disable_throttling,
)


URL = "/api/platform/system-mail/configuration/"


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class SystemMailConfigurationTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("sysadmin@platform.test", is_platform_admin=True)
        self.tenant_owner = make_user("tenant-owner@platform.test")
        self.tenant = make_tenant(self.tenant_owner, name="Tenant", slug="phase3-tenant")
        self.admin_client = auth_client(self.admin)
        self.tenant_client = auth_client(self.tenant_owner, self.tenant)
        self.proposal = dict(DEFAULT_PROPOSAL, expected_revision=0)

    def test_initial_status_explicitly_reports_legacy_runtime_unchanged(self):
        response = self.admin_client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["activation_supported"])
        self.assertFalse(response.data["mailboxes_provisioned"])
        self.assertFalse(response.data["operational"]["draft_applied"])
        self.assertEqual(response.data["operational"]["source"], "environment")
        self.assertEqual(response.data["revision"], 0)
        self.assertIsNone(response.data["draft"])

    def test_non_admin_and_anonymous_cannot_read_or_change(self):
        for client in (self.tenant_client, APIClient()):
            self.assertIn(client.get(URL).status_code, (401, 403))
            self.assertIn(client.post(URL, self.proposal, format="json").status_code, (401, 403))
        self.assertEqual(PlatformSystemMailRevision.objects.count(), 0)

    def test_saving_creates_only_a_revision_and_platform_audit(self):
        response = self.admin_client.post(URL, self.proposal, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["draft"]["revision"], 1)
        self.assertEqual(PlatformSystemMailRevision.objects.count(), 1)
        self.assertEqual(PlatformSystemMailPointer.objects.get(pk=1).revision, 1)
        self.assertFalse(response.data["operational"]["draft_applied"])
        audit = PlatformAuditLog.objects.get(action="system_mail.configuration_draft")
        self.assertEqual(audit.metadata, {"revision": 1, "state": "draft"})

    def test_revision_conflict_does_not_overwrite_or_log(self):
        self.admin_client.post(URL, self.proposal, format="json")
        response = self.admin_client.post(URL, self.proposal, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(PlatformSystemMailRevision.objects.count(), 1)
        self.assertEqual(PlatformAuditLog.objects.filter(action="system_mail.configuration_draft").count(), 1)

    def test_new_revision_preserves_immutable_history(self):
        self.admin_client.post(URL, self.proposal, format="json")
        next_revision = dict(self.proposal, expected_revision=1, sender_name="MateMail Security")
        response = self.admin_client.post(URL, next_revision, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["draft"]["revision"], 2)
        self.assertEqual(list(PlatformSystemMailRevision.objects.order_by("revision").values_list("sender_name", flat=True)), ["MateMail", "MateMail Security"])

    def test_rejects_wrong_domain_wrong_role_and_unrequested_activation(self):
        for override in (
            {"domain": "netamate.com"},
            {"domain": "mail.matemail.pro"},
            {"noreply_address": "support@netamate.com"},
            {"dmarc_address": "dmarc@mail.matemail.pro"},
            {"tlsrpt_address": "tlsrpt@other.example"},
            {"active": True},
            {"api_key": "should-not-be-stored"},
        ):
            with self.subTest(override=override):
                # DRF Serializer ignores undeclared keys unless we reject them.
                response = self.admin_client.post(URL, dict(self.proposal, **override), format="json")
                self.assertEqual(response.status_code, 400)
        self.assertEqual(PlatformSystemMailRevision.objects.count(), 0)

    def test_requires_a_platform_owned_domain_claim(self):
        MailDomainOwnership.objects.filter(
            domain="system.matemail.pro", owner_kind=MailDomainOwnerKind.PLATFORM,
        ).delete()
        response = self.admin_client.post(URL, self.proposal, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(PlatformSystemMailRevision.objects.count(), 0)

    def test_sender_name_rejects_header_injection(self):
        response = self.admin_client.post(URL, dict(self.proposal, sender_name="MateMail\nBcc: nobody@example.org"), format="json")
        self.assertEqual(response.status_code, 400)

    def test_saved_revision_cannot_be_mutated_or_deleted_in_database(self):
        response = self.admin_client.post(URL, self.proposal, format="json")
        self.assertEqual(response.status_code, 201)
        row = PlatformSystemMailRevision.objects.get(revision=1)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                PlatformSystemMailRevision.objects.filter(pk=row.pk).update(sender_name="Forged")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                PlatformSystemMailRevision.objects.filter(pk=row.pk).delete()
        self.assertEqual(PlatformSystemMailRevision.objects.get(pk=row.pk).sender_name, "MateMail")

    def test_audit_write_failure_cannot_commit_a_draft(self):
        with mock.patch(
            "apps.platform_admin.system_mail.record_platform_action",
            side_effect=RuntimeError("test-only audit write failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.admin_client.post(URL, self.proposal, format="json")
        self.assertFalse(PlatformSystemMailRevision.objects.exists())
        self.assertFalse(PlatformSystemMailPointer.objects.exists())

    def test_payload_must_be_a_json_object(self):
        response = self.admin_client.post(URL, [self.proposal], format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PlatformSystemMailRevision.objects.exists())
