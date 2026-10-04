"""Regression coverage for separate list/reader preferences and secure headers."""
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox
from apps.postbox.models import PostBoxPreference
from tests.factories import FAST_PASSWORD_HASHERS, disable_throttling, make_tenant, make_user


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class IndependentPostBoxViewTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        owner = make_user("independent-view-owner@example.test")
        tenant = make_tenant(owner, name="IndependentViews", slug="independent-views")
        domain = Domain.objects.create(
            tenant=tenant, domain="example.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.mailbox = Mailbox.objects.create(
            tenant=tenant, domain=domain, local_part="alice",
            email="alice@example.test", full_name="Alice",
            status="active", mail_engine_provisioned=True,
        )
        self.other = Mailbox.objects.create(
            tenant=tenant, domain=domain, local_part="bob",
            email="bob@example.test", full_name="Bob",
            status="active", mail_engine_provisioned=True,
        )
        self.client = self.login(self.mailbox)
        self.other_client = self.login(self.other)

    def login(self, mailbox):
        client = APIClient()
        with mock.patch("apps.postbox.auth.ratelimit.hit",
                        return_value=SimpleNamespace(allowed=True, retry_after=0)), \
             mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            result = client.post("/api/postbox/auth/login/", {
                "email": mailbox.email, "password": "secret",
            }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        return client

    def test_each_preference_saves_without_touching_the_other(self):
        initial = self.client.get("/api/postbox/preferences/")
        self.assertEqual(initial.status_code, 200, initial.data)
        self.assertEqual(initial.data["list_view"], "conversations")
        self.assertEqual(initial.data["reader_view"], "thread")

        list_change = self.client.patch("/api/postbox/preferences/", {
            "list_view": "messages",
        }, format="json")
        self.assertEqual(list_change.status_code, 200, list_change.data)
        self.assertEqual(list_change.data["reader_view"], "thread")
        self.assertEqual(self.client.get("/api/postbox/preferences/").data["list_view"], "messages")

        reader_change = self.client.patch("/api/postbox/preferences/", {
            "reader_view": "single",
        }, format="json")
        self.assertEqual(reader_change.status_code, 200, reader_change.data)
        self.assertEqual(reader_change.data["list_view"], "messages")
        preference = PostBoxPreference.objects.get(mailbox=self.mailbox)
        self.assertEqual((preference.list_view, preference.reader_view), ("messages", "single"))

        self.assertEqual(self.other_client.get("/api/postbox/preferences/").data["list_view"],
                         "conversations")
        self.assertEqual(self.other_client.get("/api/postbox/preferences/").data["reader_view"],
                         "thread")
        reader_return = self.client.patch("/api/postbox/preferences/", {
            "reader_view": "thread",
        }, format="json")
        self.assertEqual(reader_return.data["list_view"], "messages")
        self.assertEqual(reader_return.data["reader_view"], "thread")

    def test_invalid_preferences_are_rejected_without_persisting(self):
        self.assertEqual(self.client.patch("/api/postbox/preferences/", {
            "reader_view": "foobar",
        }, format="json").status_code, 400)
        self.assertEqual(self.client.patch("/api/postbox/preferences/", {
            "list_view": "thread",
        }, format="json").status_code, 400)
        saved = PostBoxPreference.objects.get(mailbox=self.mailbox)
        self.assertEqual(saved.reader_view, "thread")
        self.assertEqual(saved.list_view, "conversations")

    def test_original_headers_are_mailbox_scoped_and_uidvalidity_protected(self):
        headers = b"From: Sender <sender@example.test>\r\nSubject: Invoice\r\nX-Custom: hello\r\n\r\n"
        fake = mock.MagicMock()
        fake.select.return_value = SimpleNamespace(uid_validity=42)
        fake.fetch_headers.return_value = headers
        url = "/api/postbox/messages/INBOX/7/headers/?uid_validity=42"
        with mock.patch("apps.postbox.views_mail.imap.open_mailbox") as opened:
            opened.return_value.__enter__.return_value = fake
            result = self.client.get(url)
            self.assertEqual(result.status_code, 200, result.data)
            self.assertIn("X-Custom: hello", result.data["headers"])
            self.assertEqual(result["Cache-Control"], "private, no-store")
            fake.fetch_headers.assert_called_once_with(7)
            opened.assert_called_with(self.mailbox.email)

            stale = self.client.get(
                "/api/postbox/messages/INBOX/7/headers/?uid_validity=11"
            )
            self.assertNotEqual(stale.status_code, 200)
            fake.fetch_headers.assert_called_once_with(7)

            fake.fetch_headers.return_value = b"x" * (65536 + 1)
            oversized = self.client.get(url)
            self.assertEqual(oversized.status_code, 413)
            self.assertNotIn("headers", oversized.data)

            # Different mailbox uses its own IMAP account, not the original's.
            self.other_client.get(url)
            opened.assert_called_with(self.other.email)
