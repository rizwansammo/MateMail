"""
PostBox security: the boundaries, not the features.

The property under test throughout is that a PostBox session is bound to ONE
mailbox and confers nothing else — not another mailbox, not a Workspace
permission, not a Platform Console permission. Everything else in PostBox
depends on that being true.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import auth as postbox_auth
from apps.postbox.models import (
    Contact,
    MailRule,
    MailSignature,
    PostBoxPreference,
    PostBoxSession,
    ScheduledMessage,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    disable_throttling,
    make_tenant,
    make_user,
)

LOGIN = "/api/postbox/auth/login/"
ME = "/api/postbox/auth/me/"

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postbox-security-tests",
    }
}


def make_mailbox(tenant, domain, local_part, *, status=MailboxStatus.ACTIVE):
    return Mailbox.objects.create(
        tenant=tenant,
        domain=domain,
        local_part=local_part,
        email=f"{local_part}@{domain.domain}",
        full_name=local_part.title(),
        status=status,
        mail_engine_provisioned=True,
    )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PostBoxAuthenticationTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        disable_throttling(self)

        self.owner = make_user("owner@acme.test")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        self.domain = Domain.objects.create(
            tenant=self.tenant, domain="acme.test",
            status="active", ownership_status="verified",
            mail_engine_provisioned=True,
        )
        self.alice = make_mailbox(self.tenant, self.domain, "alice")
        self.bob = make_mailbox(self.tenant, self.domain, "bob")

    def login_as(self, mailbox, password="correct-horse"):
        """Sign in with Dovecot stubbed — this suite is about authorization."""
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            client = APIClient()
            response = client.post(
                LOGIN, {"email": mailbox.email, "password": password}, format="json"
            )
            assert response.status_code == 200, response.data
            return client

    # ── the credential is the mailbox's own ─────────────────────────────────

    def test_a_correct_mailbox_password_opens_a_session(self):
        client = self.login_as(self.alice)
        response = client.get(ME)
        self.assertEqual(200, response.status_code)
        self.assertEqual(self.alice.email, response.data["mailbox"]["email"])

    def test_a_wrong_password_is_refused(self):
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=False):
            response = APIClient().post(
                LOGIN, {"email": self.alice.email, "password": "wrong"}, format="json"
            )
        self.assertEqual(401, response.status_code)
        self.assertNotIn("Set-Cookie", response.headers.get("Set-Cookie", ""))

    def test_an_unknown_address_and_a_wrong_password_answer_identically(self):
        """Otherwise the login form enumerates a company's staff."""
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=False):
            wrong = APIClient().post(
                LOGIN, {"email": self.alice.email, "password": "wrong"}, format="json"
            )
            unknown = APIClient().post(
                LOGIN, {"email": "nobody@acme.test", "password": "wrong"}, format="json"
            )
        self.assertEqual(wrong.status_code, unknown.status_code)
        self.assertEqual(wrong.data["detail"], unknown.data["detail"])

    def test_a_suspended_mailbox_cannot_sign_in(self):
        self.alice.status = MailboxStatus.SUSPENDED
        self.alice.save(update_fields=["status"])
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True):
            response = APIClient().post(
                LOGIN, {"email": self.alice.email, "password": "correct-horse"},
                format="json",
            )
        self.assertEqual(401, response.status_code)

    def test_an_unprovisioned_mailbox_cannot_sign_in(self):
        self.alice.mail_engine_provisioned = False
        self.alice.save(update_fields=["mail_engine_provisioned"])
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True):
            response = APIClient().post(
                LOGIN, {"email": self.alice.email, "password": "correct-horse"},
                format="json",
            )
        self.assertEqual(401, response.status_code)

    def test_a_suspended_organization_blocks_every_mailbox_in_it(self):
        self.tenant.status = "suspended"
        self.tenant.save(update_fields=["status"])
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True):
            response = APIClient().post(
                LOGIN, {"email": self.alice.email, "password": "correct-horse"},
                format="json",
            )
        self.assertEqual(401, response.status_code)

    # ── the session ─────────────────────────────────────────────────────────

    def test_the_cookie_is_host_scoped_httponly_and_samesite(self):
        client = self.login_as(self.alice)
        cookie = client.cookies[postbox_auth.SESSION_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual("Lax", cookie["samesite"])
        # `__Host-` is only honoured with no Domain attribute, which is what
        # keeps the cookie off every other matemail.online host.
        self.assertTrue(postbox_auth.SESSION_COOKIE_NAME.startswith("__Host-"))
        self.assertEqual("", cookie["domain"])

    def test_no_authentication_secret_is_stored_in_the_session_row(self):
        client = self.login_as(self.alice)
        raw = client.cookies[postbox_auth.SESSION_COOKIE_NAME].value
        session = PostBoxSession.objects.get(mailbox=self.alice)

        stored = f"{session.token_hash}{session.user_agent}"
        self.assertNotIn(raw, stored)
        self.assertNotIn("correct-horse", stored)
        for forbidden in ("password", "secret", "token_plain"):
            self.assertFalse(hasattr(session, forbidden))

    def test_a_revoked_session_stops_working_immediately(self):
        client = self.login_as(self.alice)
        PostBoxSession.objects.filter(mailbox=self.alice).update(
            revoked_at=timezone.now()
        )
        self.assertEqual(403, client.get(ME).status_code)

    def test_an_expired_session_stops_working(self):
        client = self.login_as(self.alice)
        PostBoxSession.objects.filter(mailbox=self.alice).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(403, client.get(ME).status_code)

    def test_suspending_a_mailbox_ends_live_sessions(self):
        """
        Re-checked per request, not only at sign-in. The window between those
        two moments is exactly when a suspension matters.
        """
        client = self.login_as(self.alice)
        self.assertEqual(200, client.get(ME).status_code)

        self.alice.status = MailboxStatus.SUSPENDED
        self.alice.save(update_fields=["status"])

        self.assertEqual(403, client.get(ME).status_code)

    def test_logout_revokes_and_clears(self):
        client = self.login_as(self.alice)
        client.post("/api/postbox/auth/logout/")
        self.assertEqual(403, client.get(ME).status_code)

    def test_logout_all_ends_every_session_including_this_one(self):
        first = self.login_as(self.alice)
        second = self.login_as(self.alice)
        first.post("/api/postbox/auth/logout-all/")
        self.assertEqual(403, first.get(ME).status_code)
        self.assertEqual(403, second.get(ME).status_code)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PostBoxIsolationTest(TestCase):
    """Two mailboxes, in the same organization and in different ones."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        disable_throttling(self)

        self.owner_a = make_user("owner@acme.test")
        self.tenant_a = make_tenant(self.owner_a, name="Acme", slug="acme")
        self.domain_a = Domain.objects.create(
            tenant=self.tenant_a, domain="acme.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.alice = make_mailbox(self.tenant_a, self.domain_a, "alice")
        self.bob = make_mailbox(self.tenant_a, self.domain_a, "bob")

        self.owner_b = make_user("owner@globex.test")
        self.tenant_b = make_tenant(self.owner_b, name="Globex", slug="globex")
        self.domain_b = Domain.objects.create(
            tenant=self.tenant_b, domain="globex.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.carol = make_mailbox(self.tenant_b, self.domain_b, "carol")

    def login_as(self, mailbox):
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            client = APIClient()
            client.post(LOGIN, {"email": mailbox.email, "password": "x"}, format="json")
            return client

    def test_the_session_decides_the_mailbox_not_the_request(self):
        """
        No endpoint accepts a mailbox identifier, so there is nothing to tamper
        with. Sending one anyway must change nothing.
        """
        client = self.login_as(self.alice)
        response = client.get(f"{ME}?mailbox={self.bob.id}&email={self.bob.email}")
        self.assertEqual(self.alice.email, response.data["mailbox"]["email"])

    def test_contacts_are_invisible_across_mailboxes(self):
        Contact.objects.create(mailbox=self.bob, email="secret@partner.test", name="Bob's")
        Contact.objects.create(mailbox=self.alice, email="mine@friend.test", name="Alice's")

        response = self.login_as(self.alice).get("/api/postbox/contacts/")
        emails = {row["email"] for row in response.data["results"]}
        self.assertEqual({"mine@friend.test"}, emails)

    def test_another_mailboxes_contact_cannot_be_fetched_by_id(self):
        contact = Contact.objects.create(mailbox=self.bob, email="x@y.test")
        response = self.login_as(self.alice).get(f"/api/postbox/contacts/{contact.id}/")
        self.assertEqual(404, response.status_code)

    def test_another_mailboxes_contact_cannot_be_edited_or_deleted(self):
        contact = Contact.objects.create(mailbox=self.bob, email="x@y.test")
        client = self.login_as(self.alice)
        self.assertEqual(
            404,
            client.patch(f"/api/postbox/contacts/{contact.id}/",
                         {"name": "hijacked"}, format="json").status_code,
        )
        self.assertEqual(
            404, client.delete(f"/api/postbox/contacts/{contact.id}/").status_code
        )
        contact.refresh_from_db()
        self.assertNotEqual("hijacked", contact.name)

    def test_signatures_and_rules_are_scoped_too(self):
        MailSignature.objects.create(mailbox=self.bob, name="Bob", html="<p>bob</p>")
        MailRule.objects.create(
            mailbox=self.bob, name="Bob rule", field="from",
            match="contains", value="x", action="star",
        )
        client = self.login_as(self.alice)
        self.assertEqual([], client.get("/api/postbox/signatures/").data["results"])
        self.assertEqual([], client.get("/api/postbox/rules/").data["results"])

    def test_scheduled_messages_are_scoped(self):
        row = ScheduledMessage.objects.create(
            mailbox=self.bob, uid_validity=1, uid=5,
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        client = self.login_as(self.alice)
        self.assertEqual([], client.get("/api/postbox/scheduled/").data["results"])
        self.assertEqual(
            404, client.delete(f"/api/postbox/scheduled/{row.id}/").status_code
        )

    def test_a_session_from_another_organization_sees_only_its_own(self):
        Contact.objects.create(mailbox=self.carol, email="globex@x.test")
        response = self.login_as(self.alice).get("/api/postbox/contacts/")
        self.assertEqual([], response.data["results"])

    def test_another_mailboxes_session_cannot_be_revoked(self):
        bob_client = self.login_as(self.bob)
        bob_session = PostBoxSession.objects.filter(mailbox=self.bob).first()

        alice = self.login_as(self.alice)
        response = alice.delete(f"/api/postbox/security/sessions/{bob_session.id}/")
        self.assertEqual(404, response.status_code)

        # And Bob is still signed in.
        self.assertEqual(200, bob_client.get(ME).status_code)

    def test_the_session_list_shows_only_this_mailbox(self):
        self.login_as(self.bob)
        response = self.login_as(self.alice).get("/api/postbox/security/sessions/")
        self.assertEqual(1, len(response.data["results"]))
        self.assertTrue(response.data["results"][0]["current"])

    def test_no_session_token_is_ever_returned(self):
        client = self.login_as(self.alice)
        raw = client.cookies[postbox_auth.SESSION_COOKIE_NAME].value
        body = str(client.get("/api/postbox/security/sessions/").data)
        self.assertNotIn(raw, body)
        self.assertNotIn("token", body.lower())


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PostBoxPrivilegeBoundaryTest(TestCase):
    """A mailbox session must not satisfy a Workspace or Platform permission."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        disable_throttling(self)
        self.owner = make_user("owner@acme.test")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        self.domain = Domain.objects.create(
            tenant=self.tenant, domain="acme.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.alice = make_mailbox(self.tenant, self.domain, "alice")

    def login(self):
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            client = APIClient()
            client.post(LOGIN, {"email": self.alice.email, "password": "x"}, format="json")
            return client

    def test_a_postbox_session_cannot_reach_workspace_apis(self):
        client = self.login()
        for url in ("/api/workspaces/", "/api/domains/", "/api/mailboxes/",
                    "/api/teams/members/", "/api/billing/subscription/"):
            with self.subTest(url=url):
                self.assertIn(client.get(url).status_code, (401, 403, 404))

    def test_a_postbox_session_cannot_reach_platform_apis(self):
        client = self.login()
        for url in ("/api/platform/stats/", "/api/platform/tenants/",
                    "/api/platform/domains/", "/api/platform/audit/"):
            with self.subTest(url=url):
                self.assertIn(client.get(url).status_code, (401, 403))

    def test_the_identity_claims_no_privileges(self):
        identity = postbox_auth.PostBoxIdentity(self.alice, mock.Mock())
        self.assertFalse(identity.is_staff)
        self.assertFalse(identity.is_superuser)
        self.assertFalse(identity.is_platform_admin)
        self.assertFalse(identity.has_perm("anything"))

    def test_the_old_webmail_sso_bridge_is_gone(self):
        """
        The path P11 removed. It let any user with tenant access mint a login
        token for any mailbox in the organization — the exact impersonation
        PostBox's session model exists to prevent.
        """
        from django.urls import get_resolver

        resolver = get_resolver()
        for path in ("/api/webmail/sso/", "/api/internal/webmail/validate-token/"):
            with self.subTest(path=path):
                with self.assertRaises(Exception):
                    resolver.resolve(path)
