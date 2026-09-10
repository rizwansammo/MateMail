"""
Role-permission matrix.

Before Phase 0, ten mutating endpoints guarded only with HasTenantAccess, which
checks that *some* active membership exists and never looks at the role. A
read_only member could therefore delete a domain (destroying mail in the engine)
or add a forwarding rule sending a mailbox's mail to an external address.

This module drives every mutating tenant endpoint as each role and asserts the
outcome, so a permission regression fails the suite rather than shipping.
"""
from unittest import mock

from django.test import TestCase, override_settings

from apps.aliases.models import Alias, AliasStatus
from apps.domains.models import Domain
from apps.forwarding.models import ForwardingRule, ForwardingStatus
from apps.mailboxes.models import Mailbox
from apps.tenants.models import MemberRole
from tests.factories import (
    add_member,
    auth_client,
    disable_throttling,
    make_domain,
    make_mailbox,
    make_tenant,
    make_user,
)
from tests.factories import FAST_PASSWORD_HASHERS

WRITE_ROLES = (MemberRole.OWNER, MemberRole.ADMIN)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class RolePermissionMatrixTest(TestCase):
    """
    Each test issues the same request as owner, admin, support and read_only and
    asserts that only the permitted roles are allowed through. A 403 is required
    for the rest — never a 200, and never a 500.
    """

    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@example.test")
        self.tenant = make_tenant(self.owner, name="Matrix", slug="matrix")

        self.admin = make_user("admin@example.test")
        add_member(self.tenant, self.admin, MemberRole.ADMIN)
        self.support = make_user("support@example.test")
        add_member(self.tenant, self.support, MemberRole.SUPPORT)
        self.readonly = make_user("readonly@example.test")
        add_member(self.tenant, self.readonly, MemberRole.READ_ONLY)

        self.users = {
            MemberRole.OWNER: self.owner,
            MemberRole.ADMIN: self.admin,
            MemberRole.SUPPORT: self.support,
            MemberRole.READ_ONLY: self.readonly,
        }

        self.domain = make_domain(self.tenant, "matrix.example")
        self.mailbox = make_mailbox(self.tenant, self.domain, "alice")

    def client_for(self, role):
        return auth_client(self.users[role], self.tenant)

    def assert_roles(self, allowed_roles, request_fn, *, label):
        """
        request_fn(client) -> response, run once per role.
        Roles in `allowed_roles` must not get 403; all others must get exactly 403.
        """
        for role in self.users:
            with self.subTest(endpoint=label, role=role):
                res = request_fn(self.client_for(role))
                if role in allowed_roles:
                    self.assertNotEqual(
                        res.status_code, 403,
                        f"{label}: {role} should be permitted but got 403",
                    )
                    self.assertLess(
                        res.status_code, 500,
                        f"{label}: {role} triggered a server error {res.status_code}",
                    )
                else:
                    self.assertEqual(
                        res.status_code, 403,
                        f"{label}: {role} must be denied but got {res.status_code}",
                    )

    # ── Reads: every active member may read ──────────────────────────────────

    def test_all_roles_can_read_resources(self):
        for path in ["/api/domains/", "/api/mailboxes/", "/api/aliases/", "/api/forwarding/"]:
            for role in self.users:
                with self.subTest(path=path, role=role):
                    res = self.client_for(role).get(path)
                    self.assertEqual(res.status_code, 200)

    # ── Domains ──────────────────────────────────────────────────────────────

    def test_domain_create_requires_admin(self):
        counter = {"n": 0}

        def create(client):
            counter["n"] += 1
            return client.post(
                "/api/domains/", {"domain": f"new-{counter['n']}.example"}, format="json"
            )

        self.assert_roles(WRITE_ROLES, create, label="POST /api/domains/")

    def test_domain_delete_requires_admin(self):
        def delete(client):
            target = make_domain(self.tenant, f"del-{Domain.objects.count()}.example")
            return client.delete(f"/api/domains/{target.id}/")

        self.assert_roles(WRITE_ROLES, delete, label="DELETE /api/domains/{id}/")

    def test_domain_provision_requires_admin(self):
        self.assert_roles(
            WRITE_ROLES,
            lambda c: c.post(f"/api/domains/{self.domain.id}/provision/"),
            label="POST /api/domains/{id}/provision/",
        )

    # ── Mailboxes ────────────────────────────────────────────────────────────

    def test_mailbox_create_requires_admin(self):
        counter = {"n": 0}

        def create(client):
            counter["n"] += 1
            return client.post(
                "/api/mailboxes/",
                {
                    "local_part": f"user{counter['n']}",
                    "domain_id": str(self.domain.id),
                    "full_name": "New User",
                    "password": "Mailbox-Passphrase-9",
                },
                format="json",
            )

        self.assert_roles(WRITE_ROLES, create, label="POST /api/mailboxes/")

    def test_mailbox_delete_requires_admin(self):
        def delete(client):
            target = make_mailbox(
                self.tenant, self.domain, f"del{Mailbox.objects.count()}"
            )
            return client.delete(f"/api/mailboxes/{target.id}/")

        self.assert_roles(WRITE_ROLES, delete, label="DELETE /api/mailboxes/{id}/")

    def test_mailbox_status_requires_admin(self):
        self.assert_roles(
            WRITE_ROLES,
            lambda c: c.patch(
                f"/api/mailboxes/{self.mailbox.id}/status/",
                {"status": "disabled"},
                format="json",
            ),
            label="PATCH /api/mailboxes/{id}/status/",
        )

    def test_mailbox_reprovision_requires_admin(self):
        self.assert_roles(
            WRITE_ROLES,
            lambda c: c.post(
                f"/api/mailboxes/{self.mailbox.id}/reprovision/",
                {"password": "Another-Passphrase-9"},
                format="json",
            ),
            label="POST /api/mailboxes/{id}/reprovision/",
        )

    # ── Aliases ──────────────────────────────────────────────────────────────

    def test_alias_create_requires_admin(self):
        counter = {"n": 0}

        def create(client):
            counter["n"] += 1
            return client.post(
                "/api/aliases/",
                {
                    "source_local_part": f"alias{counter['n']}",
                    "domain_id": str(self.domain.id),
                    "destination_mailbox_id": str(self.mailbox.id),
                },
                format="json",
            )

        self.assert_roles(WRITE_ROLES, create, label="POST /api/aliases/")

    def test_alias_delete_requires_admin(self):
        def delete(client):
            target = Alias.objects.create(
                tenant=self.tenant,
                domain=self.domain,
                source_address=f"a{Alias.objects.count()}@matrix.example",
                destination_mailbox=self.mailbox,
                status=AliasStatus.ACTIVE,
            )
            return client.delete(f"/api/aliases/{target.id}/")

        self.assert_roles(WRITE_ROLES, delete, label="DELETE /api/aliases/{id}/")

    # ── Forwarding — the mail-exfiltration path ──────────────────────────────

    def test_forwarding_create_requires_admin(self):
        counter = {"n": 0}

        def create(client):
            counter["n"] += 1
            return client.post(
                "/api/forwarding/",
                {
                    "source_mailbox_id": str(self.mailbox.id),
                    "destination_email": f"attacker{counter['n']}@elsewhere.example",
                    "keep_copy": True,
                },
                format="json",
            )

        self.assert_roles(WRITE_ROLES, create, label="POST /api/forwarding/")

    def test_forwarding_delete_requires_admin(self):
        def delete(client):
            target = ForwardingRule.objects.create(
                tenant=self.tenant,
                source_mailbox=self.mailbox,
                destination_email=f"d{ForwardingRule.objects.count()}@elsewhere.example",
                status=ForwardingStatus.ACTIVE,
            )
            return client.delete(f"/api/forwarding/{target.id}/")

        self.assert_roles(WRITE_ROLES, delete, label="DELETE /api/forwarding/{id}/")

    def test_readonly_cannot_exfiltrate_mail_via_forwarding(self):
        """The concrete attack, asserted end to end: no rule may be created."""
        before = ForwardingRule.objects.count()
        res = self.client_for(MemberRole.READ_ONLY).post(
            "/api/forwarding/",
            {
                "source_mailbox_id": str(self.mailbox.id),
                "destination_email": "attacker@elsewhere.example",
                "keep_copy": True,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(ForwardingRule.objects.count(), before)

    def test_readonly_cannot_destroy_a_domain(self):
        res = self.client_for(MemberRole.READ_ONLY).delete(f"/api/domains/{self.domain.id}/")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Domain.objects.filter(pk=self.domain.pk).exists())

    # ── Explicitly narrower: DNS re-check is allowed for support ─────────────

    def test_dns_check_allows_support_but_not_readonly(self):
        # Stub the resolver: this test is about authorization, and the suite must
        # not depend on outbound DNS (four lookups x 5s timeout per call).
        with mock.patch(
            "apps.dnshealth.views.check_dns_for_domain", side_effect=lambda d: d
        ):
            self.assert_roles(
                (MemberRole.OWNER, MemberRole.ADMIN, MemberRole.SUPPORT),
                lambda c: c.post(f"/api/domains/{self.domain.id}/check/"),
                label="POST /api/domains/{id}/check/ (support allowlisted)",
            )

    # ── Admin-only surfaces that were already correct — pinned ───────────────

    def test_admin_only_surfaces_reject_support_and_readonly(self):
        cases = [
            ("GET", "/api/logs/", None),
            ("GET", "/api/queue/", None),
            ("GET", "/api/quarantine/", None),
            ("GET", "/api/backups/", None),
            ("GET", "/api/teams/apikeys/", None),
            ("GET", "/api/teams/invites/", None),
        ]
        for method, path, body in cases:
            self.assert_roles(
                WRITE_ROLES,
                lambda c, m=method, p=path, b=body: getattr(c, m.lower())(p),
                label=f"{method} {path}",
            )
