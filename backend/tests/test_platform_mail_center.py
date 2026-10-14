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
            for uid in uids
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
