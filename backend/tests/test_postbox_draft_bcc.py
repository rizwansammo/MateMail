"""
A saved draft keeps its Bcc so PostBox can reopen it whole, and only drafts
report one: message detail never surfaces a Bcc header on other mail, and a
sent message still never carries Bcc anywhere but the SMTP envelope.
"""
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import imap, mime
from tests.factories import FAST_PASSWORD_HASHERS, disable_throttling, make_tenant, make_user


LOGIN = "/api/postbox/auth/login/"
LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postbox-draft-bcc-tests",
    }
}
FOLDERS = [
    imap.FolderInfo(name="INBOX", role="inbox"),
    imap.FolderInfo(name="Sent", role="sent"),
    imap.FolderInfo(name="Drafts", role="drafts"),
]
PAYLOAD = {
    "from_address": "alice@example.com",
    "to": ["riley@example.net"],
    "cc": ["jordan@example.net"],
    "bcc": ["hidden@example.org"],
    "subject": "Plan",
    "text": "Draft body",
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class DraftBccTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        disable_throttling(self)

        owner = make_user("owner@example.com")
        tenant = make_tenant(owner, name="Example", slug="example")
        domain = Domain.objects.create(
            tenant=tenant,
            domain="example.com",
            status="active",
            ownership_status="verified",
            mail_engine_provisioned=True,
        )
        Mailbox.objects.create(
            tenant=tenant,
            domain=domain,
            local_part="alice",
            email="alice@example.com",
            full_name="Alice",
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            self.api = APIClient()
            response = self.api.post(
                LOGIN, {"email": "alice@example.com", "password": "x"}, format="json"
            )
            self.assertEqual(200, response.status_code)

    @staticmethod
    def _connection(opener):
        connection = opener.return_value.__enter__.return_value
        connection.list_folders.return_value = FOLDERS
        connection.select.return_value = imap.FolderInfo(name="", uid_validity=7)
        connection.append.return_value = (7, 41)
        return connection

    def _detail(self, folder: str, raw: bytes) -> dict:
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            self._connection(opener).fetch_raw.return_value = raw
            response = self.api.get(f"/api/postbox/messages/{folder}/41/")
        self.assertEqual(200, response.status_code)
        return response.json()

    def test_a_saved_draft_reopens_with_its_bcc(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            response = self.api.post("/api/postbox/drafts/", PAYLOAD, format="json")
        self.assertEqual(200, response.status_code)
        folder, raw = connection.append.call_args.args[:2]
        self.assertEqual("Drafts", folder)

        detail = self._detail("Drafts", raw)
        self.assertEqual(["riley@example.net"], detail["to"])
        self.assertEqual(["jordan@example.net"], detail["cc"])
        self.assertEqual(["hidden@example.org"], detail["bcc"])
        self.assertEqual("Plan", detail["subject"])
        self.assertEqual("Draft body", detail["text"].strip())

    def test_a_message_without_bcc_reports_none(self):
        raw = mime.build_message(
            from_address="riley@example.net",
            to=["alice@example.com"],
            subject="Hello",
            text="Hi",
        ).as_bytes()
        self.assertEqual([], self._detail("INBOX", raw)["bcc"])

    def test_a_bcc_header_outside_drafts_is_not_reported(self):
        raw = (
            b"From: riley@example.net\r\n"
            b"To: alice@example.com\r\n"
            b"Bcc: someone@example.org\r\n"
            b"Subject: Hello\r\n\r\nHi\r\n"
        )
        self.assertEqual([], self._detail("INBOX", raw)["bcc"])

    def test_sending_still_keeps_bcc_out_of_the_message(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener, \
             mock.patch("apps.postbox.sending.submit") as submit:
            self._connection(opener)
            response = self.api.post(
                "/api/postbox/compose/send/", {**PAYLOAD, "draft_uid": 41}, format="json"
            )
        self.assertEqual(200, response.status_code)
        message = submit.call_args.args[0]
        self.assertIsNone(message["Bcc"])
        self.assertNotIn("hidden@example.org", message.as_string())
        self.assertIn("hidden@example.org", submit.call_args.kwargs["recipients"])
