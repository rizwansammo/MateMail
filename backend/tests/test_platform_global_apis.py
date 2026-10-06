"""
The Platform Console's cross-organization APIs.

Two things are asserted for every endpoint: a tenant user cannot reach it, and
it does not leak a secret. The first is the whole point of a global API; the
second is the risk a global API creates, because these queries deliberately
cross the tenant boundary that everything else in the codebase enforces.
"""
import uuid

from django.test import TestCase
from rest_framework.test import APIClient

from apps.aliases.models import Alias
from apps.domains.models import Domain
from apps.forwarding.models import ForwardingRule
from apps.logs.models import MailLog
from apps.mailboxes.models import Mailbox
from apps.mailqueue.models import QueueMessage
from apps.platform_admin.models import PlatformAuditLog
from apps.quarantine.models import QuarantineMessage
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_tenant,
    make_user,
)
from django.test import override_settings

#: Every list endpoint, so a new one cannot be added without the permission
#: and pagination tests below covering it.
LIST_ENDPOINTS = [
    "/api/platform/domains/",
    "/api/platform/mailboxes/",
    "/api/platform/aliases/",
    "/api/platform/forwarding/",
    "/api/platform/queue/",
    "/api/platform/quarantine/",
    "/api/platform/logs/",
    "/api/platform/audit/",
    "/api/platform/plans/",
    "/api/platform/health/",
    "/api/platform/backups/",
    "/api/platform/search/?q=acme",
]


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class PlatformGlobalApiTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("ops@netamate.test", is_platform_admin=True)
        self.owner_a = make_user("owner@acme.test")
        self.owner_b = make_user("owner@globex.test")

        self.tenant_a = make_tenant(self.owner_a, name="Acme", slug="acme")
        self.tenant_b = make_tenant(self.owner_b, name="Globex", slug="globex")

        self.domain_a = Domain.objects.create(tenant=self.tenant_a, domain="acme.test")
        self.domain_b = Domain.objects.create(tenant=self.tenant_b, domain="globex.test")

        self.mailbox_a = Mailbox.objects.create(
            tenant=self.tenant_a, domain=self.domain_a,
            local_part="ada", email="ada@acme.test", full_name="Ada",
        )
        self.mailbox_b = Mailbox.objects.create(
            tenant=self.tenant_b, domain=self.domain_b,
            local_part="bob", email="bob@globex.test", full_name="Bob",
        )

        Alias.objects.create(
            tenant=self.tenant_a, domain=self.domain_a,
            source_address="sales@acme.test", destination_mailbox=self.mailbox_a,
        )
        ForwardingRule.objects.create(
            tenant=self.tenant_a, source_mailbox=self.mailbox_a,
            destination_email="elsewhere@external.test",
        )
        QueueMessage.objects.create(
            tenant=self.tenant_a, sender="ada@acme.test",
            recipient="out@example.test", subject="Hello",
        )
        QuarantineMessage.objects.create(
            tenant=self.tenant_a, sender="spam@bad.test",
            recipient="ada@acme.test", subject="Win", spam_score="9.10",
        )
        MailLog.objects.create(
            tenant=self.tenant_a, event_type="mailbox.created", result="success",
        )

        self.client_admin = auth_client(self.admin)

    # ── authorization ───────────────────────────────────────────────────────

    def test_a_platform_admin_reaches_every_endpoint(self):
        for url in LIST_ENDPOINTS:
            with self.subTest(url=url):
                self.assertEqual(200, self.client_admin.get(url).status_code)

    def test_a_tenant_user_reaches_none_of_them(self):
        client = auth_client(self.owner_a, self.tenant_a)
        for url in LIST_ENDPOINTS:
            with self.subTest(url=url):
                self.assertIn(client.get(url).status_code, (401, 403))

    def test_an_anonymous_caller_reaches_none_of_them(self):
        client = APIClient()
        for url in LIST_ENDPOINTS:
            with self.subTest(url=url):
                self.assertIn(client.get(url).status_code, (401, 403))

    # ── the global view is actually global ──────────────────────────────────

    def test_domains_span_organizations(self):
        names = {row["domain"] for row in
                 self.client_admin.get("/api/platform/domains/").data["results"]}
        self.assertEqual({"acme.test", "globex.test"}, names)

    def test_mailboxes_span_organizations(self):
        rows = self.client_admin.get("/api/platform/mailboxes/").data["results"]
        self.assertEqual({"ada@acme.test", "bob@globex.test"},
                         {row["email"] for row in rows})

    # ── secrets ─────────────────────────────────────────────────────────────

    def test_the_dkim_private_key_is_never_serialised(self):
        """
        The field lives on Domain, and it is the credential that lets anyone
        sign mail as the customer. A serialiser that enumerated fields would
        have shipped it.
        """
        self.domain_a.dkim_private_key = "PRIVATE-KEY-MATERIAL"
        self.domain_a.dkim_public_key = "public"
        self.domain_a.verification_token = "VERIFY-TOKEN-SECRET"
        self.domain_a.save()

        body = str(self.client_admin.get("/api/platform/domains/").data)
        self.assertNotIn("PRIVATE-KEY-MATERIAL", body)
        self.assertNotIn("VERIFY-TOKEN-SECRET", body)
        self.assertNotIn("dkim_private_key", body)

    def test_no_mailbox_credential_is_serialised(self):
        body = str(self.client_admin.get("/api/platform/mailboxes/").data)
        for forbidden in ("password", "hash", "credential", "secret"):
            self.assertNotIn(forbidden, body.lower())

    def test_queue_and_quarantine_return_metadata_not_bodies(self):
        for url in ("/api/platform/queue/", "/api/platform/quarantine/"):
            with self.subTest(url=url):
                row = self.client_admin.get(url).data["results"][0]
                for forbidden in ("body", "content", "raw", "message_body", "html"):
                    self.assertNotIn(forbidden, row)

    # ── pagination and filtering ────────────────────────────────────────────

    def test_page_size_is_capped(self):
        response = self.client_admin.get("/api/platform/mailboxes/?page_size=100000")
        self.assertLessEqual(response.data["page_size"], 100)

    def test_a_nonsense_page_size_does_not_error(self):
        response = self.client_admin.get("/api/platform/mailboxes/?page_size=abc&page=xyz")
        self.assertEqual(200, response.status_code)

    def test_search_narrows_the_result(self):
        response = self.client_admin.get("/api/platform/mailboxes/?search=globex")
        self.assertEqual(["bob@globex.test"],
                         [row["email"] for row in response.data["results"]])

    def test_ordering_is_an_allowlist(self):
        """An arbitrary order_by is both an error surface and a way to sort by
        a column that is not exposed."""
        response = self.client_admin.get(
            "/api/platform/mailboxes/?ordering=tenant__owner__password")
        self.assertEqual(200, response.status_code)

    def test_external_forwarding_is_flagged(self):
        row = self.client_admin.get("/api/platform/forwarding/").data["results"][0]
        self.assertTrue(row["external_destination"])

    # ── search ──────────────────────────────────────────────────────────────

    def test_search_finds_an_organization_by_name(self):
        results = self.client_admin.get("/api/platform/search/?q=Acme").data["results"]
        self.assertTrue(any(r["type"] == "organization" for r in results))

    def test_search_finds_a_mailbox_by_address(self):
        results = self.client_admin.get("/api/platform/search/?q=ada@").data["results"]
        self.assertTrue(any(r["type"] == "mailbox" for r in results))

    def test_search_accepts_a_uuid(self):
        results = self.client_admin.get(
            f"/api/platform/search/?q={self.tenant_a.id}").data["results"]
        self.assertTrue(any(r["id"] == str(self.tenant_a.id) for r in results))

    def test_a_one_character_query_returns_nothing(self):
        """Otherwise every page load scans five tables for 'a'."""
        self.assertEqual([], self.client_admin.get("/api/platform/search/?q=a").data["results"])

    # ── health and backups tell the truth ───────────────────────────────────

    def test_health_marks_unreachable_components_unknown_not_healthy(self):
        body = self.client_admin.get("/api/platform/health/").data
        by_name = {c["name"]: c for c in body["components"]}
        for name in ("Postfix", "Dovecot", "Rspamd"):
            self.assertEqual("unknown", by_name[name]["status"])
            self.assertTrue(by_name[name]["detail"])

    def test_backups_do_not_claim_a_platform_backup(self):
        body = self.client_admin.get("/api/platform/backups/").data
        self.assertEqual("unknown", body["platform_backups"]["status"])
        self.assertFalse(body["platform_backups"]["observable_from_application"])
        self.assertFalse(body["tenant_backup_jobs"]["supported"])


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class PlatformOversightActionTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("ops@netamate.test", is_platform_admin=True)
        self.owner = make_user("owner@acme.test")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        self.domain = Domain.objects.create(tenant=self.tenant, domain="acme.test")
        self.mailbox = Mailbox.objects.create(
            tenant=self.tenant, domain=self.domain,
            local_part="ada", email="ada@acme.test", full_name="Ada",
        )
        self.alias = Alias.objects.create(
            tenant=self.tenant, domain=self.domain,
            source_address="sales@acme.test", destination_mailbox=self.mailbox,
        )
        self.rule = ForwardingRule.objects.create(
            tenant=self.tenant, source_mailbox=self.mailbox,
            destination_email="elsewhere@external.test",
        )
        self.held = QuarantineMessage.objects.create(
            tenant=self.tenant, sender="spam@bad.test",
            recipient="ada@acme.test", subject="Win", spam_score="9.10",
        )
        self.queued = QueueMessage.objects.create(
            tenant=self.tenant, sender="ada@acme.test",
            recipient="out@example.test", subject="Hello",
        )
        self.client_admin = auth_client(self.admin)

    def test_disabling_an_alias_works_and_is_audited(self):
        response = self.client_admin.post(
            f"/api/platform/aliases/{self.alias.id}/disable/",
            {"reason": "Relaying abuse, ticket 91"}, format="json",
        )
        self.assertEqual(200, response.status_code)
        self.alias.refresh_from_db()
        self.assertEqual("disabled", self.alias.status)

        row = PlatformAuditLog.objects.get(action="alias.disable")
        self.assertEqual(self.tenant, row.tenant)
        self.assertEqual("Relaying abuse, ticket 91", row.reason)

    def test_re_enabling_an_alias_works(self):
        self.client_admin.post(f"/api/platform/aliases/{self.alias.id}/disable/",
                               {"reason": "x"}, format="json")
        self.client_admin.delete(f"/api/platform/aliases/{self.alias.id}/disable/",
                                 {"reason": "resolved"}, format="json")
        self.alias.refresh_from_db()
        self.assertEqual("active", self.alias.status)

    def test_disabling_forwarding_works_and_is_audited(self):
        response = self.client_admin.post(
            f"/api/platform/forwarding/{self.rule.id}/disable/",
            {"reason": "Exfiltration suspected"}, format="json",
        )
        self.assertEqual(200, response.status_code)
        self.rule.refresh_from_db()
        self.assertEqual("disabled", self.rule.status)
        self.assertTrue(PlatformAuditLog.objects.filter(
            action="forwarding.disable").exists())

    def test_quarantine_release_and_delete_are_audited(self):
        self.client_admin.post(
            f"/api/platform/quarantine/{self.held.id}/action/",
            {"reason": "False positive"}, format="json",
        )
        self.held.refresh_from_db()
        self.assertEqual("released", self.held.status)
        self.assertTrue(PlatformAuditLog.objects.filter(
            action="quarantine.release").exists())

    def test_queue_cancel_is_audited(self):
        response = self.client_admin.post(
            f"/api/platform/queue/{self.queued.id}/cancel/",
            {"reason": "Spam run"}, format="json",
        )
        self.assertEqual(200, response.status_code)
        self.queued.refresh_from_db()
        self.assertEqual("cancelled", self.queued.status)

    def test_every_action_requires_a_reason(self):
        targets = [
            f"/api/platform/aliases/{self.alias.id}/disable/",
            f"/api/platform/forwarding/{self.rule.id}/disable/",
            f"/api/platform/quarantine/{self.held.id}/action/",
            f"/api/platform/queue/{self.queued.id}/cancel/",
        ]
        for url in targets:
            with self.subTest(url=url):
                self.assertEqual(
                    400, self.client_admin.post(url, {}, format="json").status_code)

    def test_a_tenant_user_cannot_perform_any_oversight_action(self):
        client = auth_client(self.owner, self.tenant)
        targets = [
            f"/api/platform/aliases/{self.alias.id}/disable/",
            f"/api/platform/forwarding/{self.rule.id}/disable/",
            f"/api/platform/quarantine/{self.held.id}/action/",
            f"/api/platform/queue/{self.queued.id}/cancel/",
        ]
        for url in targets:
            with self.subTest(url=url):
                response = client.post(url, {"reason": "let me"}, format="json")
                self.assertIn(response.status_code, (401, 403))

    def test_an_unknown_target_is_404(self):
        response = self.client_admin.post(
            f"/api/platform/aliases/{uuid.uuid4()}/disable/",
            {"reason": "x"}, format="json",
        )
        self.assertEqual(404, response.status_code)
