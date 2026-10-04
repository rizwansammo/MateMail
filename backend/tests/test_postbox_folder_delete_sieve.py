"""Deletion is non-destructive; ManageSieve negotiates verified TLS end-to-end."""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox
from apps.postbox import sieve
from apps.postbox.models import FolderAppearance, MailRule
from tests.factories import FAST_PASSWORD_HASHERS, disable_throttling, make_tenant, make_user


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class SafeFolderDeletionTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        owner = make_user("folder-delete-owner@example.test")
        tenant = make_tenant(owner, name="SafeFolderDeletion", slug="safe-folder-deletion")
        domain = Domain.objects.create(
            tenant=tenant, domain="example.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.mailbox = Mailbox.objects.create(
            tenant=tenant, domain=domain, local_part="alice",
            email="alice@example.test", full_name="Alice",
            status="active", mail_engine_provisioned=True,
        )
        self.other_mailbox = Mailbox.objects.create(
            tenant=tenant, domain=domain, local_part="bob",
            email="bob@example.test", full_name="Bob",
            status="active", mail_engine_provisioned=True,
        )
        self.client = self.login(self.mailbox)
        self.other = self.login(self.other_mailbox)

    def login(self, mailbox):
        client = APIClient()
        with mock.patch("apps.postbox.auth.ratelimit.hit",
                        return_value=SimpleNamespace(allowed=True, retry_after=0)), \
             mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.auth.imap.open_mailbox"):
            result = client.post("/api/postbox/auth/login/", {
                "email": mailbox.email, "password": "dummy",
            }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        return client

    @staticmethod
    def imap_mock(count=0, uids=None):
        imap = mock.MagicMock()
        imap.list_folders.return_value = [
            SimpleNamespace(name="INBOX", role="inbox", selectable=True),
            SimpleNamespace(name="Finance", role="", selectable=True),
        ]
        imap.folder_counts.return_value = (count, 0)
        imap.search_uids.return_value = [] if uids is None else uids
        return imap

    @staticmethod
    def active_rule(mailbox):
        return MailRule.objects.create(
            mailbox=mailbox, name="Invoices", field="from", match="contains",
            value="invoice", action=MailRule.Action.MOVE,
            action_folder="Finance", enabled=True,
        )

    def test_nonempty_folder_preview_and_delete_do_not_move_anything(self):
        fake = self.imap_mock(count=2, uids=[7, 9])
        with mock.patch("apps.postbox.views_mail.imap.open_mailbox") as opened:
            opened.return_value.__enter__.return_value = fake
            preview = self.client.post("/api/postbox/folders/delete-check/",
                                       {"name": "Finance"}, format="json")
            self.assertEqual(preview.status_code, 200, preview.data)
            self.assertEqual(preview.data["message_count"], 2)
            self.assertFalse(preview.data["can_delete"])
            result = self.client.delete("/api/postbox/folders/Finance/")
            self.assertEqual(result.status_code, 409, result.data)
            self.assertEqual(result.data["code"], "folder_not_empty")
            fake.move.assert_not_called()
            fake.delete_folder.assert_not_called()

    def test_new_delivery_after_preview_is_rechecked(self):
        fake = self.imap_mock()
        with mock.patch("apps.postbox.views_mail.imap.open_mailbox") as opened:
            opened.return_value.__enter__.return_value = fake
            before = self.client.post("/api/postbox/folders/delete-check/",
                                      {"name": "Finance"}, format="json")
            self.assertTrue(before.data["can_delete"])
            fake.folder_counts.return_value = (1, 0)
            response = self.client.delete("/api/postbox/folders/Finance/")
            self.assertEqual(response.status_code, 409)
            fake.delete_folder.assert_not_called()

    def test_active_rule_blocks_delete_without_altering_rule_or_sieve(self):
        rule = self.active_rule(self.mailbox)
        fake = self.imap_mock()
        with mock.patch("apps.postbox.views_mail.imap.open_mailbox") as opened, \
             mock.patch("apps.postbox.views_settings._sync_sieve") as synced:
            opened.return_value.__enter__.return_value = fake
            preview = self.client.post("/api/postbox/folders/delete-check/",
                                       {"name": "Finance"}, format="json")
            self.assertEqual(preview.data["active_rule_count"], 1)
            self.assertFalse(preview.data["can_delete"])
            result = self.client.delete("/api/postbox/folders/Finance/")
            self.assertEqual(result.status_code, 409, result.data)
            self.assertEqual(result.data["code"], "folder_has_rules")
            rule.refresh_from_db()
            self.assertTrue(rule.enabled)
            fake.delete_folder.assert_not_called()
            synced.assert_not_called()
            # A different mailbox cannot see this user's active rules.
            other = self.other.post("/api/postbox/folders/delete-check/",
                                    {"name": "Finance"}, format="json")
            self.assertEqual(other.data["active_rule_count"], 0)

    def test_empty_folder_deletes_even_if_sieve_service_is_unavailable(self):
        appearance = FolderAppearance.objects.create(
            mailbox=self.mailbox, name="Finance", color="#2563eb",
        )
        fake = self.imap_mock()
        with mock.patch("apps.postbox.views_mail.imap.open_mailbox") as opened, \
             mock.patch("apps.postbox.views_settings._sync_sieve",
                        side_effect=RuntimeError("Sieve offline")) as synced:
            opened.return_value.__enter__.return_value = fake
            result = self.client.delete("/api/postbox/folders/Finance/")
            self.assertEqual(result.status_code, 204)
            fake.delete_folder.assert_called_once_with("Finance")
            fake.move.assert_not_called()
            synced.assert_not_called()
            self.assertFalse(FolderAppearance.objects.filter(pk=appearance.pk).exists())

    def test_standard_folders_cannot_be_deleted_or_previewed(self):
        fake = self.imap_mock()
        with mock.patch("apps.postbox.views_mail.imap.open_mailbox") as opened:
            opened.return_value.__enter__.return_value = fake
            self.assertEqual(self.client.post("/api/postbox/folders/delete-check/",
                {"name": "INBOX"}, format="json").status_code, 400)
            self.assertEqual(self.client.delete("/api/postbox/folders/INBOX/").status_code, 400)
            fake.delete_folder.assert_not_called()


class FakeSieveSocket:
    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.closed = False

    def recv(self, _):
        return self.replies.pop(0) if self.replies else b""

    def sendall(self, data):
        self.writes.append(data)

    def close(self):
        self.closed = True


@override_settings(
    DEBUG=False, POSTBOX_SIEVE_HOST="mx.matemail.online",
    POSTBOX_SIEVE_PORT=4190, POSTBOX_SIEVE_STARTTLS=True,
    POSTBOX_SIEVE_TLS_SERVER_NAME="mx.matemail.online",
    POSTBOX_MASTER_USER="postbox", POSTBOX_MASTER_PASSWORD="test-only",
)
class ManageSieveStartTlsTest(SimpleTestCase):
    def test_capability_greeting_and_starttls_are_fully_negotiated(self):
        plain = FakeSieveSocket([
            b'"IMPLEMENTATION" "Dovecot"\r\n"SASL" ""\r\n"STARTTLS"\r\nOK "Ready"\r\n',
            b'OK "Begin TLS"\r\n',
        ])
        secure = FakeSieveSocket([
            b'"IMPLEMENTATION" "Dovecot"\r\n"SASL" "PLAIN"\r\nOK "Ready"\r\n',
            b'OK "Authenticated"\r\n', b'OK "Script saved"\r\n',
            b'OK "Activated"\r\n',
        ])
        with mock.patch("apps.postbox.sieve.socket.create_connection",
                        return_value=plain) as connect, \
             mock.patch("apps.postbox.sieve.ssl.create_default_context") as tls:
            tls.return_value.wrap_socket.return_value = secure
            sieve.install_script("alice@example.test", 'keep;')
        connect.assert_called_once_with(("mx.matemail.online", 4190), timeout=15)
        self.assertEqual(plain.writes, [b"STARTTLS\r\n"])
        tls.return_value.wrap_socket.assert_called_once_with(
            plain, server_hostname="mx.matemail.online",
        )
        self.assertTrue(secure.writes[0].startswith(b'AUTHENTICATE "PLAIN"'))
        self.assertTrue(secure.writes[1].startswith(b'PUTSCRIPT "matemail-postbox"'))
        self.assertIn(b'SETACTIVE "matemail-postbox"\r\n', secure.writes)

    def test_starttls_refusal_never_transmits_master_credential(self):
        plain = FakeSieveSocket([b'OK "Ready"\r\n', b'NO "TLS unavailable"\r\n'])
        with mock.patch("apps.postbox.sieve.socket.create_connection", return_value=plain):
            with self.assertRaises(sieve.SieveError):
                sieve.install_script("alice@example.test", "keep;")
        self.assertEqual(plain.writes, [b"STARTTLS\r\n"])

    @override_settings(POSTBOX_SIEVE_STARTTLS=False)
    def test_production_refuses_plaintext_master_authentication(self):
        plain = FakeSieveSocket([b'OK "Ready"\r\n'])
        with mock.patch("apps.postbox.sieve.socket.create_connection", return_value=plain):
            with self.assertRaises(sieve.SieveError):
                sieve.install_script("alice@example.test", "keep;")
        self.assertFalse(any(b"AUTHENTICATE" in write for write in plain.writes))
