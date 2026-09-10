"""
Cross-tenant isolation at the HTTP layer.

tests/test_tenant_isolation.py covers the ORM managers. These tests drive the
real API with a real token, because that is the surface an attacker uses: knowing
another tenant's object UUID must not be enough to read, mutate or delete it.

Phase 0 changed the permission classes on ten of these endpoints, so this module
also guards against a permission refactor accidentally widening tenant scope.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.aliases.models import Alias, AliasStatus
from apps.domains.models import Domain
from apps.forwarding.models import ForwardingRule, ForwardingStatus
from apps.mailboxes.models import Mailbox
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_mailbox,
    make_tenant,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cross-tenant-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class CrossTenantApiTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)

        # Tenant A — the attacker. Owner role, so role is never the limiting factor.
        self.user_a = make_user("a-owner@example.test")
        self.tenant_a = make_tenant(self.user_a, name="Tenant A", slug="tenant-a")
        self.client_a = auth_client(self.user_a, self.tenant_a)

        # Tenant B — the victim.
        self.user_b = make_user("b-owner@example.test")
        self.tenant_b = make_tenant(self.user_b, name="Tenant B", slug="tenant-b")
        self.domain_b = make_domain(self.tenant_b, "victim.example")
        self.mailbox_b = make_mailbox(self.tenant_b, self.domain_b, "bob")
        self.alias_b = Alias.objects.create(
            tenant=self.tenant_b,
            domain=self.domain_b,
            source_address="sales@victim.example",
            destination_mailbox=self.mailbox_b,
            status=AliasStatus.ACTIVE,
        )
        self.rule_b = ForwardingRule.objects.create(
            tenant=self.tenant_b,
            source_mailbox=self.mailbox_b,
            destination_email="archive@victim.example",
            status=ForwardingStatus.ACTIVE,
        )

    # ── Listing leaks nothing ────────────────────────────────────────────────

    def test_lists_contain_only_own_tenant_objects(self):
        cases = [
            ("/api/domains/", str(self.domain_b.id)),
            ("/api/mailboxes/", str(self.mailbox_b.id)),
            ("/api/aliases/", str(self.alias_b.id)),
            ("/api/forwarding/", str(self.rule_b.id)),
        ]
        for path, foreign_id in cases:
            with self.subTest(path=path):
                res = self.client_a.get(path)
                self.assertEqual(res.status_code, 200)
                ids = {str(item["id"]) for item in res.data}
                self.assertNotIn(foreign_id, ids)
                self.assertEqual(ids, set())

    # ── Direct reads by known UUID are 404 ───────────────────────────────────

    def test_reading_foreign_objects_by_id_is_404(self):
        for path in [
            f"/api/domains/{self.domain_b.id}/",
            f"/api/mailboxes/{self.mailbox_b.id}/",
            f"/api/domains/{self.domain_b.id}/records/",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client_a.get(path).status_code, 404)

    # ── Mutations on foreign objects are refused and change nothing ──────────

    def test_deleting_a_foreign_domain_is_refused(self):
        res = self.client_a.delete(f"/api/domains/{self.domain_b.id}/")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(Domain.objects.filter(pk=self.domain_b.pk).exists())

    def test_deleting_a_foreign_mailbox_is_refused(self):
        res = self.client_a.delete(f"/api/mailboxes/{self.mailbox_b.id}/")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(Mailbox.objects.filter(pk=self.mailbox_b.pk).exists())

    def test_deleting_a_foreign_alias_is_refused(self):
        res = self.client_a.delete(f"/api/aliases/{self.alias_b.id}/")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(Alias.objects.filter(pk=self.alias_b.pk).exists())

    def test_deleting_a_foreign_forwarding_rule_is_refused(self):
        res = self.client_a.delete(f"/api/forwarding/{self.rule_b.id}/")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(ForwardingRule.objects.filter(pk=self.rule_b.pk).exists())

    def test_disabling_a_foreign_mailbox_is_refused(self):
        res = self.client_a.patch(
            f"/api/mailboxes/{self.mailbox_b.id}/status/",
            {"status": "disabled"},
            format="json",
        )
        self.assertEqual(res.status_code, 404)
        self.mailbox_b.refresh_from_db()
        self.assertEqual(self.mailbox_b.status, "active")

    def test_reprovisioning_a_foreign_mailbox_is_refused(self):
        res = self.client_a.post(
            f"/api/mailboxes/{self.mailbox_b.id}/reprovision/",
            {"password": "Attacker-Passphrase-9"},
            format="json",
        )
        self.assertEqual(res.status_code, 404)

    def test_provisioning_a_foreign_domain_is_refused(self):
        res = self.client_a.post(f"/api/domains/{self.domain_b.id}/provision/")
        self.assertEqual(res.status_code, 404)

    # ── Creating objects attached to another tenant's parent is refused ──────

    def test_cannot_attach_a_mailbox_to_a_foreign_domain(self):
        before = Mailbox.objects.count()
        res = self.client_a.post(
            "/api/mailboxes/",
            {
                "local_part": "intruder",
                "domain_id": str(self.domain_b.id),
                "full_name": "Intruder",
                "password": "Mailbox-Passphrase-9",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Mailbox.objects.count(), before)

    def test_cannot_attach_an_alias_to_a_foreign_domain(self):
        before = Alias.objects.count()
        res = self.client_a.post(
            "/api/aliases/",
            {
                "source_local_part": "intruder",
                "domain_id": str(self.domain_b.id),
                "destination_address": "attacker@elsewhere.example",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Alias.objects.count(), before)

    def test_cannot_forward_a_foreign_mailbox(self):
        """The exfiltration path: redirect another tenant's mail to yourself."""
        before = ForwardingRule.objects.count()
        res = self.client_a.post(
            "/api/forwarding/",
            {
                "source_mailbox_id": str(self.mailbox_b.id),
                "destination_email": "attacker@elsewhere.example",
                "keep_copy": True,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(ForwardingRule.objects.count(), before)

    # ── Workspace-scoped endpoints ───────────────────────────────────────────

    def test_cannot_read_a_foreign_workspace(self):
        for path in [
            f"/api/workspaces/{self.tenant_b.id}/",
            f"/api/workspaces/{self.tenant_b.id}/stats/",
            f"/api/workspaces/{self.tenant_b.id}/members/",
            f"/api/workspaces/{self.tenant_b.id}/onboarding/",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client_a.get(path).status_code, 404)

    def test_cannot_rename_a_foreign_workspace(self):
        res = self.client_a.patch(
            f"/api/workspaces/{self.tenant_b.id}/", {"name": "Seized"}, format="json"
        )
        self.assertEqual(res.status_code, 404)
        self.tenant_b.refresh_from_db()
        self.assertEqual(self.tenant_b.name, "Tenant B")

    def test_cannot_switch_into_a_foreign_workspace(self):
        res = self.client_a.post(
            "/api/workspaces/switch/", {"tenant_id": str(self.tenant_b.id)}, format="json"
        )
        self.assertEqual(res.status_code, 404)

    def test_platform_admin_endpoints_are_closed_to_tenant_owners(self):
        for path in ["/api/platform/stats/", "/api/platform/tenants/"]:
            with self.subTest(path=path):
                self.assertEqual(self.client_a.get(path).status_code, 403)
