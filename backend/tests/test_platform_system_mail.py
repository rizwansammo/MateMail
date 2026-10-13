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
        self.assertFalse(response.data["staging_request_enabled"])
        self.assertFalse(response.data["operational"]["draft_applied"])
        self.assertEqual(response.data["operational"]["source"], "environment")
        self.assertEqual(response.data["revision"], 0)
        self.assertIsNone(response.data["draft"])

    @override_settings(
        SYSTEM_MAIL_STAGING_ENABLED=True,
        NATIVE_ENGINE_PLATFORM_SECRET="distinct-platform-secret-at-least-32-characters",
        NATIVE_ENGINE_API_SECRET="different-regular-engine-secret",
    )
    def test_staging_gate_only_reports_configuration_and_not_activation(self):
        response = self.admin_client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["staging_request_enabled"])
        self.assertFalse(response.data["activation_supported"])
        self.assertFalse(response.data["mailboxes_provisioned"])

    @override_settings(
        SYSTEM_MAIL_STAGING_ENABLED=True,
        NATIVE_ENGINE_PLATFORM_SECRET="same",
        NATIVE_ENGINE_API_SECRET="same",
    )
    def test_shared_or_weak_platform_secret_hides_staging(self):
        response = self.admin_client.get(URL)
        self.assertFalse(response.data["staging_request_enabled"])

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


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class SystemMailReadinessTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("readiness-admin@platform.test", is_platform_admin=True)
        self.customer = make_user("readiness-customer@platform.test")
        self.tenant = make_tenant(self.customer, name="Customer", slug="phase3-readiness")
        self.admin_client = auth_client(self.admin)
        self.tenant_client = auth_client(self.customer, self.tenant)

    def test_only_platform_admin_can_read_readiness_and_history(self):
        for url in ("/api/platform/system-mail/readiness/", "/api/platform/system-mail/history/"):
            with self.subTest(url=url):
                self.assertEqual(self.admin_client.get(url).status_code, 200)
                self.assertIn(self.tenant_client.get(url).status_code, (401, 403))
                self.assertIn(APIClient().get(url).status_code, (401, 403))

    def test_stub_adapter_does_not_claim_engine_ready(self):
        with override_settings(MAIL_ENGINE_ADAPTER="stub"):
            with mock.patch("apps.platform_admin.system_mail_readiness.get_adapter") as getter:
                response = self.admin_client.get("/api/platform/system-mail/readiness/")
        self.assertEqual(response.status_code, 200)
        getter.assert_not_called()
        self.assertEqual(response.data["engine"]["status"], "unknown")
        self.assertFalse(response.data["cutover_ready"])
        self.assertFalse(response.data["activation_supported"])
        self.assertEqual(response.data["outstanding_gates"]["smtp_delivery"], "not_tested")

    def test_readiness_calls_only_read_operations_and_hides_customer_inventory(self):
        from apps.mail_engine.dto import EngineDomain, EngineMailbox
        adapter = mock.Mock()
        adapter.list_domains.return_value = [
            EngineDomain("mail.matemail.pro", active=True),
            EngineDomain("system.matemail.pro", active=False),
            EngineDomain("sensitive-customer.example", active=True),
        ]
        adapter.list_mailboxes.side_effect = [
            [EngineMailbox("noreply@mail.matemail.pro", active=True)],
            [EngineMailbox("security@system.matemail.pro", active=False),
             EngineMailbox("unknown@system.matemail.pro", active=True)],
        ]
        with override_settings(MAIL_ENGINE_ADAPTER="native"):
            with mock.patch("apps.platform_admin.system_mail_readiness.get_adapter", return_value=adapter):
                response = self.admin_client.get("/api/platform/system-mail/readiness/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(adapter.list_domains.call_count, 1)
        self.assertEqual(adapter.list_mailboxes.call_args_list, [
            mock.call("mail.matemail.pro"), mock.call("system.matemail.pro"),
        ])
        for method in ("ensure_domain", "ensure_mailbox", "delete_domain", "rotate_dkim_key"):
            getattr(adapter, method).assert_not_called()
        self.assertEqual(response.data["engine"]["legacy_sender"], "active")
        self.assertEqual(response.data["engine"]["target_roles"]["security_address"], "inactive")
        self.assertEqual(response.data["engine"]["target_roles"]["dmarc_address"], "absent")
        self.assertNotIn("sensitive-customer.example", str(response.data))
        self.assertNotIn("unknown@system.matemail.pro", str(response.data))
        self.assertFalse(response.data["cutover_ready"])

    def test_unreachable_engine_never_leaks_sensitive_exception_text(self):
        adapter = mock.Mock()
        adapter.list_domains.side_effect = RuntimeError(
            "private-api.example:8443 Authorization=Bearer LEAKED_SECRET"
        )
        with override_settings(MAIL_ENGINE_ADAPTER="native"):
            with mock.patch("apps.platform_admin.system_mail_readiness.get_adapter", return_value=adapter):
                response = self.admin_client.get("/api/platform/system-mail/readiness/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["engine"]["status"], "unknown")
        self.assertNotIn("LEAKED_SECRET", str(response.data))
        self.assertFalse(response.data["cutover_ready"])

    def test_platform_ownership_missing_is_blocker(self):
        MailDomainOwnership.objects.filter(domain="system.matemail.pro").delete()
        response = self.admin_client.get("/api/platform/system-mail/readiness/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["platform_ownership"]["target"], "blocked")
        self.assertFalse(response.data["cutover_ready"])

    def test_draft_creation_does_not_change_operational_readiness(self):
        before = self.admin_client.get("/api/platform/system-mail/readiness/").data
        response = self.admin_client.post(
            "/api/platform/system-mail/configuration/",
            dict(DEFAULT_PROPOSAL, expected_revision=0),
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        after = self.admin_client.get("/api/platform/system-mail/readiness/").data
        self.assertEqual(after["revision"], 1)
        self.assertTrue(after["draft_present"])
        self.assertFalse(after["cutover_ready"])
        self.assertEqual(after["operational"], before["operational"])
        self.assertEqual(after["outstanding_gates"], before["outstanding_gates"])

    def test_history_returns_immutable_revisions_newest_first(self):
        endpoint = "/api/platform/system-mail/configuration/"
        self.assertEqual(self.admin_client.post(
            endpoint, dict(DEFAULT_PROPOSAL, expected_revision=0), format="json"
        ).status_code, 201)
        self.assertEqual(self.admin_client.post(
            endpoint, dict(DEFAULT_PROPOSAL, expected_revision=1, sender_name="MateMail Security"),
            format="json",
        ).status_code, 201)
        history = self.admin_client.get("/api/platform/system-mail/history/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual([x["revision"] for x in history.data["results"]], [2, 1])
        self.assertFalse(history.data["has_more"])
        self.assertNotIn("password", str(history.data).lower())



@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class SystemMailRuntimePreviewTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("preview-admin@platform.test", is_platform_admin=True)
        self.client = auth_client(self.admin)

    @override_settings(
        DEFAULT_FROM_EMAIL="MateMail <noreply@mail.matemail.pro>",
        DMARC_REPORT_ADDRESS="dmarc@mail.matemail.pro",
        TLS_RPT_REPORT_ADDRESS="tlsrpt@mail.matemail.pro",
        MAIL_ENGINE_ADAPTER="stub",
    )
    def test_legacy_runtime_aligned_when_no_draft(self):
        response = self.client.get("/api/platform/system-mail/readiness/")
        self.assertEqual(response.status_code, 200)
        state = response.data["runtime_alignment"]
        self.assertEqual(state["roles"], {
            "sender": "legacy",
            "dmarc_receiver": "legacy",
            "tlsrpt_receiver": "legacy",
        })
        self.assertTrue(state["legacy_configuration_intact"])
        self.assertFalse(state["migration_started"])
        self.assertEqual(response.data["provisioning_preview"]["domain"], "unknown")
        self.assertFalse(response.data["provisioning_preview"]["changes_applied"])

    @override_settings(
        DEFAULT_FROM_EMAIL="MateMail <noreply@system.matemail.pro>",
        DMARC_REPORT_ADDRESS="dmarc@mail.matemail.pro",
        TLS_RPT_REPORT_ADDRESS="tlsrpt@another.example",
        MAIL_ENGINE_ADAPTER="stub",
    )
    def test_unplanned_runtime_address_drift_is_explicit(self):
        response = self.client.get("/api/platform/system-mail/readiness/")
        state = response.data["runtime_alignment"]
        self.assertEqual(state["roles"], {
            "sender": "target",
            "dmarc_receiver": "legacy",
            "tlsrpt_receiver": "other",
        })
        self.assertFalse(state["legacy_configuration_intact"])
        self.assertTrue(state["migration_started"])
        self.assertFalse(response.data["cutover_ready"])
        self.assertFalse(response.data["activation_supported"])

    @override_settings(MAIL_ENGINE_ADAPTER="native")
    def test_engine_inventory_creates_only_a_preview(self):
        from apps.mail_engine.dto import EngineDomain, EngineMailbox
        adapter = mock.Mock()
        adapter.list_domains.return_value = [
            EngineDomain("mail.matemail.pro", active=True),
            EngineDomain("system.matemail.pro", active=True),
        ]
        adapter.list_mailboxes.side_effect = [
            [EngineMailbox("noreply@mail.matemail.pro", active=True)],
            [EngineMailbox("noreply@system.matemail.pro", active=True),
             EngineMailbox("security@system.matemail.pro", active=False)],
        ]
        with mock.patch(
            "apps.platform_admin.system_mail_readiness.get_adapter", return_value=adapter
        ):
            response = self.client.get("/api/platform/system-mail/readiness/")
        self.assertEqual(response.status_code, 200)
        preview = response.data["provisioning_preview"]
        self.assertEqual(preview["domain"], "present")
        self.assertEqual(preview["mailboxes"]["noreply_address"], "present")
        self.assertEqual(preview["mailboxes"]["security_address"], "would_need_activation")
        self.assertEqual(preview["mailboxes"]["dmarc_address"], "would_need_provisioning")
        self.assertFalse(preview["changes_applied"])
        self.assertFalse(response.data["cutover_ready"])
        for operation in ("ensure_domain", "ensure_mailbox", "delete_domain", "set_mailbox_password"):
            getattr(adapter, operation).assert_not_called()

    @override_settings(MAIL_ENGINE_ADAPTER="native")
    def test_engine_unavailable_never_claims_provisioning_complete(self):
        adapter = mock.Mock()
        adapter.list_domains.side_effect = TimeoutError("API bearer secret should not leak")
        with mock.patch(
            "apps.platform_admin.system_mail_readiness.get_adapter", return_value=adapter
        ):
            response = self.client.get("/api/platform/system-mail/readiness/")
        self.assertEqual(response.status_code, 200)
        preview = response.data["provisioning_preview"]
        self.assertEqual(preview["domain"], "unknown")
        self.assertTrue(all(x == "unknown" for x in preview["mailboxes"].values()))
        self.assertNotIn("bearer secret", str(response.data))
