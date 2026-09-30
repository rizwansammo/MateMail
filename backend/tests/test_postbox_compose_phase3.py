"""Regression coverage for the premium PostBox compose lifecycle."""

import email
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import imap, mime, tasks
from apps.postbox.models import MailSignature, ScheduledMessage, SignatureKind
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    disable_throttling,
    make_tenant,
    make_user,
)


LOGIN = "/api/postbox/auth/login/"
LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postbox-compose-phase3-tests",
    }
}
FOLDERS = [
    imap.FolderInfo(name="INBOX", role="inbox"),
    imap.FolderInfo(name="Sent", role="sent"),
    imap.FolderInfo(name="Drafts", role="drafts"),
    imap.FolderInfo(name="Scheduled", role="scheduled"),
]


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PostBoxComposePhase3Test(TestCase):
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
        self.mailbox = Mailbox.objects.create(
            tenant=tenant,
            domain=domain,
            local_part="alice",
            email="alice@example.com",
            full_name="Alice Example",
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        self.signature = MailSignature.objects.create(
            mailbox=self.mailbox,
            name="Formal",
            kind=SignatureKind.TEXT,
            text="Regards,\nAlice",
        )

        self.api = APIClient()
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            response = self.api.post(
                LOGIN,
                {"email": self.mailbox.email, "password": "x"},
                format="json",
            )
        self.assertEqual(200, response.status_code)

    @staticmethod
    def _connection(opener):
        connection = opener.return_value.__enter__.return_value
        connection.list_folders.return_value = FOLDERS
        connection.select.return_value = imap.FolderInfo(
            name="INBOX",
            uid_validity=7,
        )
        connection.append.return_value = (9, 41)
        return connection

    @staticmethod
    def _source_with_attachment() -> bytes:
        return mime.build_message(
            from_address="riley@example.net",
            to=["alice@example.com"],
            subject="Source",
            text="Source body",
            attachments=[
                ("proposal.pdf", "application/pdf", b"%PDF-phase3"),
            ],
        ).as_bytes()

    def _compose_payload(self, **extra):
        return {
            "from_address": self.mailbox.email,
            "to": ["client@example.net"],
            "bcc": ["hidden@example.net"],
            "subject": "Proposal",
            "text": "Please review.",
            **extra,
        }

    def test_forward_context_returns_authoritative_attachment_references(self):
        raw = self._source_with_attachment()
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            connection.fetch_raw.return_value = raw

            response = self.api.get(
                "/api/postbox/messages/INBOX/17/reply-context/?mode=forward"
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(1, len(body["attachments"]))
        ref = body["attachments"][0]
        self.assertEqual("INBOX", ref["folder"])
        self.assertEqual(17, ref["uid"])
        self.assertEqual(7, ref["uid_validity"])
        self.assertEqual("proposal.pdf", ref["filename"])
        self.assertTrue(ref["part_id"])

    def test_saving_forwarded_attachment_remaps_it_to_the_new_draft(self):
        source = self._source_with_attachment()
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            connection.fetch_raw.return_value = source

            response = self.api.post(
                "/api/postbox/drafts/",
                self._compose_payload(
                    existing_attachments=[
                        {
                            "folder": "INBOX",
                            "uid": 17,
                            "uid_validity": 7,
                            "part_id": "2",
                        }
                    ]
                ),
                format="json",
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(1, len(body["attachments"]))
        saved_ref = body["attachments"][0]
        self.assertEqual("Drafts", saved_ref["folder"])
        self.assertEqual(41, saved_ref["uid"])
        self.assertEqual(9, saved_ref["uid_validity"])
        self.assertEqual("proposal.pdf", saved_ref["filename"])

        appended = connection.append.call_args.args[1]
        parsed = mime.parse_message(appended, load_remote_images=True)
        self.assertEqual(["proposal.pdf"], [a.filename for a in parsed.attachments])
        self.assertTrue(parsed.draft_state)

    def test_schedule_stores_editable_source_with_bcc_and_signature_metadata(self):
        future = timezone.now() + timedelta(hours=2)

        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            response = self.api.post(
                "/api/postbox/compose/send/",
                self._compose_payload(
                    signature_id=str(self.signature.id),
                    send_at=future.isoformat(),
                ),
                format="json",
            )

        self.assertEqual(201, response.status_code)
        raw = connection.append.call_args.args[1]
        message = email.message_from_bytes(raw)
        self.assertEqual("1", message[mime.DRAFT_STATE_HEADER])
        self.assertEqual(
            str(self.signature.id),
            message[mime.DRAFT_SIGNATURE_HEADER],
        )
        self.assertEqual("hidden@example.net", message["Bcc"])
        self.assertNotIn(b"Regards", raw)

        row = ScheduledMessage.objects.get(mailbox=self.mailbox)
        self.assertEqual("Scheduled", row.folder)
        self.assertEqual(41, row.uid)

    def test_worker_finalises_new_scheduled_source_once_and_preserves_bcc_envelope(self):
        source = mime.build_message(
            from_address=self.mailbox.email,
            to=["client@example.net"],
            bcc=["hidden@example.net"],
            subject="Later",
            text="Scheduled body",
            attachments=[
                ("plan.txt", "text/plain", b"attachment-body"),
            ],
            keep_bcc=True,
            draft_signature_id=str(self.signature.id),
        )
        row = ScheduledMessage.objects.create(
            mailbox=self.mailbox,
            folder="Scheduled",
            uid_validity=9,
            uid=41,
            subject="Later",
            recipients="client@example.net, hidden@example.net",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )

        with mock.patch("apps.postbox.imap.open_mailbox") as opener, \
             mock.patch("apps.postbox.sending.submit") as submit:
            connection = self._connection(opener)
            connection.select.return_value = imap.FolderInfo(
                name="Scheduled",
                uid_validity=9,
            )
            connection.fetch_raw.return_value = source.as_bytes()

            outcome = tasks.send_scheduled_message(str(row.id))

        self.assertEqual("sent", outcome)
        submitted = submit.call_args.args[0]
        self.assertIsNone(submitted["Bcc"])
        self.assertIsNone(submitted[mime.DRAFT_STATE_HEADER])
        self.assertIsNone(submitted[mime.DRAFT_SIGNATURE_HEADER])
        self.assertEqual(
            ["client@example.net", "hidden@example.net"],
            submit.call_args.kwargs["recipients"],
        )
        parsed = mime.parse_message(submitted.as_bytes(), load_remote_images=True)
        self.assertEqual(1, parsed.text.count("Regards,\nAlice"))
        self.assertEqual(["plan.txt"], [a.filename for a in parsed.attachments])

        sent_bytes = connection.append.call_args.args[1]
        sent = email.message_from_bytes(sent_bytes)
        self.assertIsNone(sent["Bcc"])
        self.assertIsNone(sent[mime.DRAFT_STATE_HEADER])
        connection.delete_permanently.assert_called_once_with([41])

    def test_cancelled_new_schedule_can_return_to_drafts_without_losing_metadata(self):
        raw = mime.build_message(
            from_address=self.mailbox.email,
            to=["client@example.net"],
            bcc=["hidden@example.net"],
            subject="Later",
            text="Scheduled body",
            keep_bcc=True,
            draft_signature_id=str(self.signature.id),
        ).as_bytes()
        row = ScheduledMessage.objects.create(
            mailbox=self.mailbox,
            folder="Scheduled",
            uid_validity=9,
            uid=41,
            subject="Later",
            recipients="client@example.net, hidden@example.net",
            scheduled_at=timezone.now() + timedelta(hours=1),
        )

        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            connection.fetch_raw.return_value = raw

            response = self.api.delete(f"/api/postbox/scheduled/{row.id}/")

        self.assertEqual(200, response.status_code)
        connection.move.assert_called_once_with([41], "Drafts")

        parsed = mime.parse_message(raw, load_remote_images=True)
        self.assertTrue(parsed.draft_state)
        self.assertEqual(["hidden@example.net"], parsed.bcc)
        self.assertEqual(str(self.signature.id), parsed.draft_signature_id)
