"""
A saved draft keeps the body as written and the chosen signature as
metadata (`X-PostBox-Signature-Id`), so it reopens with the same choice and
nothing is baked in. Sending applies the signature once and never carries
the draft header, and a signature the mailbox does not have is refused
instead of silently dropped.
"""
import email
import uuid
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import imap, mime
from apps.postbox.models import MailSignature, SignatureKind
from tests.factories import FAST_PASSWORD_HASHERS, disable_throttling, make_tenant, make_user


LOGIN = "/api/postbox/auth/login/"
HEADER = mime.DRAFT_SIGNATURE_HEADER
LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postbox-draft-signature-tests",
    }
}
FOLDERS = [
    imap.FolderInfo(name="INBOX", role="inbox"),
    imap.FolderInfo(name="Sent", role="sent"),
    imap.FolderInfo(name="Drafts", role="drafts"),
]
#: A 1x1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6364f8ff1f00000300010081b3b7"
    "0000000049454e44ae426082"
)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class DraftSignatureTest(TestCase):
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
        mailboxes = [
            Mailbox.objects.create(
                tenant=tenant,
                domain=domain,
                local_part=local,
                email=f"{local}@example.com",
                full_name=local.title(),
                status=MailboxStatus.ACTIVE,
                mail_engine_provisioned=True,
            )
            for local in ("alice", "bob")
        ]
        self.alice, self.bob = mailboxes
        self.formal = MailSignature.objects.create(
            mailbox=self.alice, name="Formal", kind=SignatureKind.TEXT,
            text="Regards,\nAlice",
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

    def _payload(self, **extra):
        return {
            "from_address": "alice@example.com",
            "to": ["riley@example.net"],
            "bcc": ["hidden@example.org"],
            "subject": "Plan",
            "text": "Draft body",
            **extra,
        }

    def _save(self, **extra):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            response = self.api.post(
                "/api/postbox/drafts/", self._payload(**extra), format="json"
            )
        return response, connection

    def _detail(self, folder: str, raw: bytes) -> dict:
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            self._connection(opener).fetch_raw.return_value = raw
            response = self.api.get(f"/api/postbox/messages/{folder}/41/")
        self.assertEqual(200, response.status_code)
        return response.json()

    @staticmethod
    def _raw(extra_headers: str = "") -> bytes:
        return (
            "From: riley@example.net\r\nTo: alice@example.com\r\n"
            f"{extra_headers}Subject: Hello\r\n\r\nHi\r\n"
        ).encode()

    def test_a_draft_stores_the_body_and_the_chosen_signature_apart(self):
        response, connection = self._save(signature_id=str(self.formal.id))
        self.assertEqual(200, response.status_code)
        raw = connection.append.call_args.args[1]
        self.assertEqual(str(self.formal.id), email.message_from_bytes(raw)[HEADER])
        self.assertNotIn(b"Regards", raw)

        detail = self._detail("Drafts", raw)
        self.assertEqual(str(self.formal.id), detail["signature_id"])
        self.assertFalse(detail["signature_missing"])
        self.assertEqual("Draft body", detail["text"].strip())
        self.assertEqual(["hidden@example.org"], detail["bcc"])

    def test_replacing_a_draft_keeps_the_chosen_signature(self):
        response, connection = self._save(
            signature_id=str(self.formal.id), draft_uid=40, text="Draft body, v2"
        )
        self.assertEqual(200, response.status_code)
        raw = connection.append.call_args.args[1]
        self.assertEqual(str(self.formal.id), email.message_from_bytes(raw)[HEADER])
        connection.delete_permanently.assert_called_once_with([40])

    def test_a_draft_without_a_signature_reports_none(self):
        _, connection = self._save()
        raw = connection.append.call_args.args[1]
        self.assertIsNone(email.message_from_bytes(raw)[HEADER])
        detail = self._detail("Drafts", raw)
        self.assertIsNone(detail["signature_id"])
        self.assertFalse(detail["signature_missing"])

    def test_html_and_image_signatures_are_not_flattened_into_a_draft(self):
        brand = MailSignature.objects.create(
            mailbox=self.alice, name="Brand", kind=SignatureKind.HTML,
            html="<p><b>Alice Park</b></p>", text="Alice Park",
        )
        logo = MailSignature.objects.create(
            mailbox=self.alice, name="Logo", kind=SignatureKind.IMAGE,
            image_alt="Example logo", image_data=PNG, image_content_type="image/png",
        )
        for signature in (brand, logo):
            _, connection = self._save(signature_id=str(signature.id))
            message = email.message_from_bytes(connection.append.call_args.args[1])
            self.assertEqual("text/plain", message.get_content_type())
            self.assertEqual(str(signature.id), message[HEADER])
            body = message.get_payload(decode=True)
            self.assertNotIn(b"Alice Park", body)
            self.assertNotIn(b"Example logo", body)

    def test_signature_metadata_outside_drafts_is_never_reported(self):
        raw = self._raw(f"{HEADER}: {self.formal.id}\r\n")
        detail = self._detail("INBOX", raw)
        self.assertIsNone(detail["signature_id"])
        self.assertFalse(detail["signature_missing"])

    def test_a_draft_whose_signature_is_gone_or_foreign_reports_it_missing(self):
        foreign = MailSignature.objects.create(
            mailbox=self.bob, name="Bob", kind=SignatureKind.TEXT, text="Bob"
        )
        for value in (uuid.uuid4(), foreign.id, "not-a-uuid"):
            detail = self._detail("Drafts", self._raw(f"{HEADER}: {value}\r\n"))
            self.assertIsNone(detail["signature_id"], value)
            self.assertTrue(detail["signature_missing"], value)

    def test_sending_applies_the_signature_once_without_the_draft_header(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener, \
             mock.patch("apps.postbox.sending.submit") as submit:
            connection = self._connection(opener)
            response = self.api.post(
                "/api/postbox/compose/send/",
                self._payload(signature_id=str(self.formal.id), draft_uid=41),
                format="json",
            )
        self.assertEqual(200, response.status_code)
        message = submit.call_args.args[0]
        self.assertEqual(1, message.get_content().count("Regards,\nAlice"))
        self.assertIsNone(message[HEADER])
        self.assertIsNone(message["Bcc"])
        sent_copy = connection.append.call_args.args[1]
        self.assertNotIn(HEADER.encode(), sent_copy)

    def test_an_unavailable_or_foreign_signature_is_refused(self):
        foreign = MailSignature.objects.create(
            mailbox=self.bob, name="Bob", kind=SignatureKind.TEXT, text="Bob"
        )
        for signature_id in (str(uuid.uuid4()), str(foreign.id)):
            response, connection = self._save(signature_id=signature_id)
            self.assertEqual(400, response.status_code)
            self.assertIn("signature_id", response.json())
            connection.append.assert_not_called()

            with mock.patch("apps.postbox.imap.open_mailbox") as opener, \
                 mock.patch("apps.postbox.sending.submit") as submit:
                self._connection(opener)
                response = self.api.post(
                    "/api/postbox/compose/send/",
                    self._payload(signature_id=signature_id),
                    format="json",
                )
            self.assertEqual(400, response.status_code)
            self.assertIn("signature_id", response.json())
            submit.assert_not_called()
