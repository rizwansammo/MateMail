"""Phase 6A: Platform Mail Center access and read-only mailbox invariants."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.platform_admin.models import PlatformAuditLog
from tests.factories import (
    FAST_PASSWORD_HASHERS, auth_client, disable_throttling,
    make_tenant, make_user,
)

FOLDERS = "/api/platform/mail-center/folders/"
MESSAGES = "/api/platform/mail-center/messages/"


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.folders = [
            SimpleNamespace(name="INBOX", role="inbox", selectable=True),
            SimpleNamespace(name="Sent", role="sent", selectable=True),
            SimpleNamespace(name="Hidden", role="", selectable=False),
        ]

    def list_folders(self):
        self.calls.append("list")
        return self.folders

    def folder_counts(self, folder):
        self.calls.append(("counts", folder))
        return (4, 2)

    def select(self, folder, readonly=False):
        assert readonly is True, "IMAP SELECT MUST be read-only"
        self.calls.append(("select_readonly", folder))
        return SimpleNamespace(uid_validity=42)

    def search_uids(self, criteria, *, newest=True):
        assert criteria == ["ALL"] and newest
        self.calls.append("search")
        return [9, 8, 7]

    def fetch_summaries(self, uids):
        self.calls.append(("headers", uids))
        return [
            SimpleNamespace(
                uid=uid, subject="Sensitive operations test",
                from_name="Platform", from_address="test@example.test",
                date="2026-10-14T10:00:00Z", seen=False,
                has_attachments=False, size=128
            )
            for uid in uids if uid in (9, 8, 7)
        ]

    def fetch_raw(self, *args):
        raise AssertionError("Message bodies must not be fetched")

    def fetch_previews(self, *args):
        raise AssertionError("Body previews must not be fetched")

    def store_flags(self, *args):
        raise AssertionError("Flags must not be mutated")


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class PlatformMailCenterTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("mail-center-admin@platform.test", is_platform_admin=True)
        self.customer = make_user("mail-center-customer@platform.test")
        tenant = make_tenant(self.customer, slug="platform-mail-center-customer")
        self.admin_client = auth_client(self.admin)
        self.customer_client = auth_client(self.customer, tenant)
        self.connection = FakeConnection()
        self.opened_addresses = []

        @contextmanager
        def opened(address):
            self.opened_addresses.append(address)
            yield self.connection

        p = mock.patch("apps.platform_admin.mail_center_views.imap.open_mailbox", side_effect=opened)
        p.start()
        self.addCleanup(p.stop)

    def test_disabled_by_default_and_no_imap(self):
        for endpoint in (FOLDERS, MESSAGES):
            result = self.admin_client.get(endpoint)
            self.assertEqual(result.status_code, 503)
            self.assertNotIn("test@example.test", str(result.data))
        self.assertEqual(self.opened_addresses, [])
        self.assertFalse(PlatformAuditLog.objects.exists())

    @override_settings(PLATFORM_MAIL_CENTER_ENABLED=True, POSTBOX_MASTER_PASSWORD="test-only")
    def test_platform_admin_only_and_no_tenant_mailbox_access(self):
        for endpoint in (FOLDERS, MESSAGES):
            self.assertIn(APIClient().get(endpoint).status_code, (401, 403))
            self.assertIn(self.customer_client.get(endpoint).status_code, (401, 403))
        self.assertEqual(self.opened_addresses, [])
        for endpoint in (FOLDERS, MESSAGES):
            forged = self.admin_client.get(endpoint, {"mailbox": "owner@customer.test"})
            self.assertEqual(forged.status_code, 400)
        self.assertEqual(self.opened_addresses, [])

    @override_settings(PLATFORM_MAIL_CENTER_ENABLED=True, POSTBOX_MASTER_PASSWORD="")
    def test_missing_master_credential_fails_closed(self):
        self.assertEqual(self.admin_client.get(FOLDERS).status_code, 503)
        self.assertEqual(self.opened_addresses, [])

    @override_settings(PLATFORM_MAIL_CENTER_ENABLED=True, POSTBOX_MASTER_PASSWORD="test-only")
    def test_folders_are_readonly_bounded_and_audited(self):
        response = self.admin_client.get(FOLDERS)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([x["name"] for x in response.data["results"]], ["INBOX", "Sent"])
        self.assertEqual(self.opened_addresses, ["operations@system.matemail.pro"])
        self.assertEqual(self.connection.calls, ["list", ("counts", "INBOX"), ("counts", "Sent")])
        self.assertEqual(response["Cache-Control"].lower().split(",")[0], "max-age=0")
        audit = PlatformAuditLog.objects.get(action="mail_center.folders_read")
        self.assertEqual(audit.target_label, "operations@system.matemail.pro")
        self.assertEqual(audit.metadata, {"folder_count": 2})

    @override_settings(PLATFORM_MAIL_CENTER_ENABLED=True, POSTBOX_MASTER_PASSWORD="test-only")
    def test_message_headers_readonly_paged_and_audited(self):
        response = self.admin_client.get(MESSAGES, {"page_size": 2, "page": 2})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 3)
        self.assertEqual([m["uid"] for m in response.data["results"]], [7])
        self.assertEqual(response.data["uid_validity"], 42)
        self.assertEqual(self.opened_addresses, ["operations@system.matemail.pro"])
        self.assertEqual(self.connection.calls, ["list", ("select_readonly", "INBOX"), "search", ("headers", [7])])
        self.assertNotIn("body", str(response.data).lower())
        audit = PlatformAuditLog.objects.get(action="mail_center.messages_read")
        self.assertEqual(audit.metadata, {"folder_role": "inbox", "page": 2, "count": 1})

    @override_settings(PLATFORM_MAIL_CENTER_ENABLED=True, POSTBOX_MASTER_PASSWORD="test-only")
    def test_rejects_unlisted_folders_and_unbounded_requests(self):
        for params in (
            {"folder": "security@system.matemail.pro"},
            {"folder": "Hidden"},
        ):
            self.assertEqual(self.admin_client.get(MESSAGES, params).status_code, 404)
        for params in (
            {"page_size": 51}, {"page": 101},
            {"page_size": "abc"}, {"page": "-1"},
            {"folder": "INBOX", "address": "security@system.matemail.pro"},
            {"folder": "A"*256},
        ):
            self.assertEqual(self.admin_client.get(MESSAGES, params).status_code, 400)
        self.assertFalse(PlatformAuditLog.objects.exists())

    @override_settings(PLATFORM_MAIL_CENTER_ENABLED=True, POSTBOX_MASTER_PASSWORD="test-only")
    def test_mail_access_failure_is_generic_and_does_not_log_secret(self):
        with mock.patch(
            "apps.platform_admin.mail_center_views.imap.open_mailbox",
            side_effect=RuntimeError("Bearer SECRET_LEAK mailbox password"),
        ):
            response = self.admin_client.get(FOLDERS)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("SECRET_LEAK", str(response.data))
        self.assertFalse(PlatformAuditLog.objects.exists())

    @override_settings(PLATFORM_MAIL_CENTER_ENABLED=True, POSTBOX_MASTER_PASSWORD="test-only")
    def test_read_only_methods_no_mutation(self):
        for endpoint in (FOLDERS, MESSAGES):
            self.assertEqual(self.admin_client.post(endpoint, {}, format="json").status_code, 405)
            self.assertEqual(self.admin_client.delete(endpoint).status_code, 405)
        self.assertEqual(self.opened_addresses, [])


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    PLATFORM_MAIL_CENTER_ENABLED=True,
    POSTBOX_MASTER_PASSWORD="only-for-tests",
)
class PlatformMailCenterDetailTests(TestCase):
    def setUp(self):
        from email.message import EmailMessage
        disable_throttling(self)
        self.admin = make_user("detail-admin@platform.test", is_platform_admin=True)
        self.customer = make_user("detail-customer@platform.test")
        tenant = make_tenant(self.customer, slug="mail-center-detail-tenant")
        self.client = auth_client(self.admin)
        self.customer_client = auth_client(self.customer, tenant)
        self.connection = FakeConnection()
        message = EmailMessage()
        message["From"] = "Tester <sender@example.test>"
        message["To"] = "operations@system.matemail.pro"
        message["Subject"] = "Operations urgent request"
        message.set_content("Please review this request.")
        message.add_alternative(
            '<p>Hello <strong>Operations</strong> '
            '<img src="https://tracking.example/pixel"></p>'
            '<script>alert("xss")</script><a href="javascript:alert(1)">bad</a>',
            subtype="html",
        )
        message.add_attachment(b"safe payload", maintype="text", subtype="plain",
                               filename="report.txt")
        self.raw = message.as_bytes()
        self.connection.fetch_raw = mock.Mock(side_effect=self._fetch_raw)
        self.seen_accounts = []

        @contextmanager
        def opened(address):
            self.seen_accounts.append(address)
            yield self.connection

        p = mock.patch("apps.platform_admin.mail_center_views.imap.open_mailbox", side_effect=opened)
        p.start()
        self.addCleanup(p.stop)
        self.details_url = "/api/platform/mail-center/messages/9/"
        self.attachment_url = "/api/platform/mail-center/messages/9/attachments/3/"
        self.valid_query = {"folder": "INBOX", "uid_validity": "42"}

    def _fetch_raw(self, uid):
        self.connection.calls.append(("raw", uid))
        return self.raw

    def test_refuses_tenant_anonymous_and_cross_mailbox_selector(self):
        for url in (self.details_url, self.attachment_url):
            self.assertIn(self.customer_client.get(url, self.valid_query).status_code, (401, 403))
            self.assertIn(APIClient().get(url, self.valid_query).status_code, (401, 403))
            params = dict(self.valid_query, mailbox="security@system.matemail.pro")
            self.assertEqual(self.client.get(url, params).status_code, 400)
        self.assertEqual(self.seen_accounts, [])

    def test_disabled_feature_fails_closed_before_opening_imap(self):
        with override_settings(PLATFORM_MAIL_CENTER_ENABLED=False):
            self.assertEqual(self.client.get(self.details_url, self.valid_query).status_code, 503)
            self.assertEqual(self.client.get(self.attachment_url, self.valid_query).status_code, 503)
        self.assertEqual(self.seen_accounts, [])

    def test_requires_valid_uidvalidity_and_validates_before_fetch_raw(self):
        for url in (self.details_url, self.attachment_url):
            for query, status in (
                ({"folder": "INBOX"}, 400),
                ({"uid_validity": "broken"}, 400),
                ({"uid_validity": "41"}, 409),
                ({"uid_validity": "42", "folder": "Hidden"}, 404),
                ({"uid_validity": "42", "folder": "customer@evil.test"}, 404),
            ):
                self.connection.calls.clear()
                self.assertEqual(self.client.get(url, query).status_code, status)
                self.assertNotIn(("raw", 9), self.connection.calls)
        self.assertEqual(PlatformAuditLog.objects.count(), 0)

    def test_detail_sanitizes_html_blocks_remote_and_never_mutates(self):
        response = self.client.get(self.details_url, self.valid_query)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["uid_validity"], 42)
        self.assertEqual(response.data["text"], "Please review this request.")
        self.assertNotIn("<script", response.data["html"])
        self.assertNotIn("javascript:", response.data["html"])
        self.assertNotIn("tracking.example", response.data["html"])
        self.assertTrue(response.data["remote_images_blocked"])
        self.assertEqual(response.data["from"]["address"], "sender@example.test")
        self.assertEqual(len(response.data["attachments"]), 1)
        self.assertTrue(response.data["attachments"][0]["downloadable"])
        self.assertEqual(self.seen_accounts, ["operations@system.matemail.pro"])
        self.assertIn(("select_readonly", "INBOX"), self.connection.calls)
        self.assertEqual(PlatformAuditLog.objects.get(action="mail_center.message_read").metadata,
                         {"folder_role": "inbox"})
        self.assertNotIn("Operations urgent request", str(PlatformAuditLog.objects.first().metadata))
        self.assertIn("no-store", response["Cache-Control"])

    def test_bcc_survives_draft_reopen_but_never_leaks_from_inbox(self):
        from apps.postbox import mime
        draft = mime.build_message(
            from_address="operations@system.matemail.pro",
            to=["recipient@example.test"], cc=[], bcc=["hidden@example.test"],
            subject="Draft privacy", text="Sensitive Bcc must survive edit",
            keep_bcc=True,
        )
        self.raw = draft.as_bytes()
        inbox = self.client.get(self.details_url, self.valid_query)
        self.assertEqual(inbox.status_code, 200, inbox.data)
        self.assertEqual(inbox.data["bcc"], [])
        self.connection.folders.append(
            SimpleNamespace(name="Drafts", role="drafts", selectable=True),
        )
        stored = self.client.get(self.details_url, {"folder": "Drafts", "uid_validity": "42"})
        self.assertEqual(stored.status_code, 200, stored.data)
        self.assertEqual(stored.data["bcc"], ["hidden@example.test"])
        self.assertNotIn("hidden@example.test", str(
            PlatformAuditLog.objects.values_list("metadata", flat=True)
        ))

    def test_download_is_bounded_and_forced_attachment(self):
        detail = self.client.get(self.details_url, self.valid_query)
        part_id = detail.data["attachments"][0]["part_id"]
        response = self.client.get(
            f"/api/platform/mail-center/messages/9/attachments/{part_id}/",
            self.valid_query,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"safe payload")
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertIn("no-store", response["Cache-Control"])
        audit = PlatformAuditLog.objects.get(action="mail_center.attachment_download")
        self.assertEqual(audit.metadata, {"folder_role": "inbox", "bytes": 12})
        self.assertNotIn("report.txt", str(audit.metadata))

    def test_non_attachment_part_and_unknown_uid_rejected(self):
        self.assertEqual(
            self.client.get("/api/platform/mail-center/messages/9/attachments/0/",
                            self.valid_query).status_code, 404,
        )
        self.assertEqual(
            self.client.get("/api/platform/mail-center/messages/9/attachments/900/",
                            self.valid_query).status_code, 400,
        )
        self.assertEqual(
            self.client.get("/api/platform/mail-center/messages/10/", self.valid_query).status_code,
            404,
        )

    def test_oversize_message_rejected_before_imap_fetch(self):
        with mock.patch(
            "apps.platform_admin.mail_center_views.MAX_RAW_MESSAGE_BYTES", 16,
        ):
            response = self.client.get(self.details_url, self.valid_query)
        self.assertEqual(response.status_code, 413)
        self.assertNotIn(("raw", 9), self.connection.calls)
        self.assertFalse(PlatformAuditLog.objects.exists())

    def test_oversize_attachment_rejected_without_content(self):
        detail = self.client.get(self.details_url, self.valid_query)
        part_id = detail.data["attachments"][0]["part_id"]
        with mock.patch("apps.platform_admin.mail_center_views.MAX_ATTACHMENT_BYTES", 3):
            response = self.client.get(
                f"/api/platform/mail-center/messages/9/attachments/{part_id}/",
                self.valid_query,
            )
        self.assertEqual(response.status_code, 413)
        self.assertFalse(PlatformAuditLog.objects.filter(action="mail_center.attachment_download").exists())

    def test_mail_layer_exception_never_reveals_secrets(self):
        self.connection.fetch_raw.side_effect = RuntimeError("IMAP PASS secret-forbidden")
        response = self.client.get(self.details_url, self.valid_query)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-forbidden", str(response.data))
        self.assertEqual(self.seen_accounts, ["operations@system.matemail.pro"])
        self.assertFalse(PlatformAuditLog.objects.exists())

    def test_no_mutating_methods(self):
        for url in (self.details_url, self.attachment_url):
            self.assertEqual(self.client.post(url, self.valid_query, format="json").status_code, 405)
            self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertFalse(self.seen_accounts)
