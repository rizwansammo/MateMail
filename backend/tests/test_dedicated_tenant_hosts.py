from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.mailboxes.models import MailboxStatus
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    add_member,
    auth_client,
    make_api_key,
    make_domain,
    make_mailbox,
    make_tenant,
    make_user,
)


DEDICATED = {
    "mailadmin.netamate.com": "netamate-solutions",
    "postbox.netamate.com": "netamate-solutions",
}


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    ALLOWED_HOSTS=[
        "testserver",
        "portal.matemail.online",
        "mailadmin.netamate.com",
        "postbox.netamate.com",
    ],
    DEDICATED_TENANT_HOSTS=DEDICATED,
)
class DedicatedTenantHostTest(TestCase):
    def setUp(self):
        self.user = make_user("owner@example.test")
        self.other = make_tenant(self.user, name="Other", slug="other")
        self.netamate = make_tenant(
            self.user, name="NetaMate Solutions", slug="netamate-solutions"
        )
        add_member(self.netamate, self.user, "owner") if False else None

    def test_login_selects_the_host_bound_tenant(self):
        response = APIClient().post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
            HTTP_HOST="mailadmin.netamate.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["tenant"]["slug"], "netamate-solutions")

    def test_main_workspace_login_keeps_normal_multi_tenant_behavior(self):
        response = APIClient().post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
            HTTP_HOST="portal.matemail.online",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["tenant"]["slug"], "other")

    def test_account_without_bound_membership_is_refused(self):
        outsider = make_user("outsider@example.test")
        make_tenant(outsider, name="Outside", slug="outside")
        response = APIClient().post(
            "/api/auth/login/",
            {"email": outsider.email, "password": TEST_PASSWORD},
            format="json",
            HTTP_HOST="mailadmin.netamate.com",
        )
        self.assertEqual(response.status_code, 403)

    def test_signup_is_not_available_on_dedicated_host(self):
        response = APIClient().post(
            "/api/auth/signup/",
            {
                "email": "new@example.test",
                "password": TEST_PASSWORD,
                "full_name": "New User",
                "workspace_name": "New Workspace",
            },
            format="json",
            HTTP_HOST="mailadmin.netamate.com",
        )
        self.assertEqual(response.status_code, 404)

    def test_workspace_list_only_returns_the_bound_tenant(self):
        client = auth_client(self.user, self.netamate)
        response = client.get(
            "/api/workspaces/", HTTP_HOST="mailadmin.netamate.com"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["slug"] for row in response.data], ["netamate-solutions"])

    def test_workspace_switch_cannot_leave_the_bound_tenant(self):
        client = auth_client(self.user, self.netamate)
        response = client.post(
            "/api/workspaces/switch/",
            {"tenant_id": str(self.other.id)},
            format="json",
            HTTP_HOST="mailadmin.netamate.com",
        )
        self.assertEqual(response.status_code, 404)

    def test_api_key_for_another_tenant_is_blocked_at_middleware(self):
        raw, _ = make_api_key(self.other, self.user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        response = client.get(
            "/api/workspaces/", HTTP_HOST="mailadmin.netamate.com"
        )
        self.assertEqual(response.status_code, 403)

    def test_postbox_rejects_mailbox_from_another_tenant(self):
        domain = make_domain(self.other, "other.example")
        mailbox = make_mailbox(self.other, domain, local_part="alice")
        mailbox.status = MailboxStatus.ACTIVE
        mailbox.mail_engine_provisioned = True
        mailbox.save(update_fields=["status", "mail_engine_provisioned"])

        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True):
            response = APIClient().post(
                "/api/postbox/auth/login/",
                {"email": mailbox.email, "password": "Correct-Mailbox-Password"},
                format="json",
                HTTP_HOST="postbox.netamate.com",
            )

        self.assertEqual(response.status_code, 401)
