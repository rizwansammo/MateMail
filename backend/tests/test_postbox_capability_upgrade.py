"""Regression coverage for PostBox Phase 2.5 mailbox capabilities."""

from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import imap
from apps.postbox.models import MailRule, MessageMoveProvenance, RemoteImageSenderTrust
from apps.postbox.views_mail import _message_provenance_key
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
        "LOCATION": "postbox-capability-upgrade-tests",
    }
}


def summary(
    uid: int,
    folder: str,
    *,
    message_id: str = "",
    date: str = "Wed, 30 Sep 2026 12:00:00 +0000",
    has_attachments: bool = False,
) -> imap.MessageSummary:
    return imap.MessageSummary(
        uid=uid,
        uid_validity=7,
        folder=folder,
        message_id=message_id or f"<{uid}@example.com>",
        subject=f"Message {uid}",
        from_name="Sender",
        from_address="sender@example.net",
        to=["alice@example.com"],
        date=date,
        size=1024 + uid,
        has_attachments=has_attachments,
    )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PostBoxCapabilityUpgradeTest(TestCase):
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
        self.alice = Mailbox.objects.create(
            tenant=tenant,
            domain=domain,
            local_part="alice",
            email="alice@example.com",
            full_name="Alice",
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        self.bob = Mailbox.objects.create(
            tenant=tenant,
            domain=domain,
            local_part="bob",
            email="bob@example.com",
            full_name="Bob",
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )

        self.api = APIClient()
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            response = self.api.post(
                LOGIN,
                {"email": self.alice.email, "password": "x"},
                format="json",
            )
        self.assertEqual(200, response.status_code)

    @staticmethod
    def _connection(opener):
        return opener.return_value.__enter__.return_value

    def test_oldest_sort_is_a_real_server_order_request(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            connection.select.return_value = imap.FolderInfo(
                name="INBOX",
                role="inbox",
                uid_validity=7,
            )
            connection.search_uids.return_value = [1, 2, 3]
            connection.fetch_summaries.side_effect = lambda uids: [
                summary(uid, "INBOX") for uid in uids
            ]

            response = self.api.get(
                "/api/postbox/messages/?folder=INBOX&sort=oldest"
            )

        self.assertEqual(200, response.status_code)
        connection.search_uids.assert_called_once_with(["ALL"], newest=False)
        self.assertEqual([1, 2, 3], [row["uid"] for row in response.json()["results"]])
        self.assertEqual("oldest", response.json()["sort"])

    def test_global_search_merges_normal_folders_and_excludes_trash(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            connection.list_folders.return_value = [
                imap.FolderInfo("INBOX", role="inbox"),
                imap.FolderInfo("Sent", role="sent"),
                imap.FolderInfo("Trash", role="trash"),
            ]

            selected = {"name": ""}
            def select(name, readonly=True):
                selected["name"] = name
                return imap.FolderInfo(name=name, uid_validity=7)

            connection.select.side_effect = select
            connection.search_uids.side_effect = [[11], [22]]
            def fetch(uids):
                if selected["name"] == "INBOX":
                    return [
                        summary(
                            uids[0],
                            "INBOX",
                            date="Tue, 29 Sep 2026 09:00:00 +0000",
                        )
                    ]
                return [
                    summary(
                        uids[0],
                        "Sent",
                        date="Wed, 30 Sep 2026 09:00:00 +0000",
                    )
                ]
            connection.fetch_summaries.side_effect = fetch

            response = self.api.get(
                "/api/postbox/messages/?q=project&scope=all&sort=newest"
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("all", body["scope"])
        self.assertEqual(2, body["total"])
        self.assertEqual(
            [("Sent", 22), ("INBOX", 11)],
            [(row["folder"], row["uid"]) for row in body["results"]],
        )
        selected_folders = [call.args[0] for call in connection.select.call_args_list]
        self.assertEqual(["INBOX", "Sent"], selected_folders)

    def test_trash_then_restore_returns_to_the_original_folder(self):
        original = summary(
            11,
            "Projects/Client A",
            message_id="<restore-me@example.com>",
        )
        folder_rows = [
            imap.FolderInfo("INBOX", role="inbox"),
            imap.FolderInfo("Projects/Client A"),
            imap.FolderInfo("Trash", role="trash"),
            imap.FolderInfo("Junk", role="junk"),
        ]

        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            connection.select.return_value = imap.FolderInfo(
                name="Projects/Client A",
                uid_validity=7,
            )
            connection.list_folders.return_value = folder_rows
            connection.fetch_summaries.return_value = [original]

            response = self.api.post(
                "/api/postbox/messages/action/trash/",
                {"folder": "Projects/Client A", "uids": [11]},
                format="json",
            )

        self.assertEqual(200, response.status_code)
        connection.move.assert_called_once_with([11], "Trash")
        provenance = MessageMoveProvenance.objects.get(mailbox=self.alice)
        self.assertEqual("Projects/Client A", provenance.original_folder)

        moved = summary(
            77,
            "Trash",
            message_id="<restore-me@example.com>",
        )
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            connection.select.return_value = imap.FolderInfo(
                name="Trash",
                uid_validity=8,
            )
            connection.list_folders.return_value = folder_rows
            connection.fetch_summaries.return_value = [moved]

            response = self.api.post(
                "/api/postbox/messages/action/restore/",
                {"folder": "Trash", "uids": [77]},
                format="json",
            )

        self.assertEqual(200, response.status_code)
        connection.move.assert_called_once_with([77], "Projects/Client A")
        self.assertFalse(
            MessageMoveProvenance.objects.filter(mailbox=self.alice).exists()
        )

    def test_restore_falls_back_to_inbox_when_original_folder_is_gone(self):
        moved = summary(5, "Trash", message_id="<restore-gone@example.com>")
        MessageMoveProvenance.objects.create(
            mailbox=self.alice,
            message_key=_message_provenance_key(moved),
            original_folder="Deleted Project",
        )

        # If the recorded folder was deleted after the message entered Trash,
        # restore safely falls back to INBOX.
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self._connection(opener)
            connection.select.return_value = imap.FolderInfo(
                name="Trash",
                uid_validity=8,
            )
            connection.list_folders.return_value = [
                imap.FolderInfo("INBOX", role="inbox"),
                imap.FolderInfo("Trash", role="trash"),
            ]
            connection.fetch_summaries.return_value = [moved]

            response = self.api.post(
                "/api/postbox/messages/action/restore/",
                {"folder": "Trash", "uids": [5]},
                format="json",
            )

        self.assertEqual(200, response.status_code)
        connection.move.assert_called_once_with([5], "INBOX")

    def test_pdf_preview_is_inline_sandboxed_and_nosniff(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener, \
             mock.patch(
                 "apps.postbox.views_mail.mime.extract_attachment",
                 return_value=("report.pdf", "application/pdf", b"%PDF-1.4"),
             ):
            connection = self._connection(opener)
            connection.fetch_raw.return_value = b"raw"
            response = self.api.get(
                "/api/postbox/messages/INBOX/4/attachments/2/preview/"
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response["Content-Type"])
        self.assertTrue(response["Content-Disposition"].startswith("inline;"))
        self.assertEqual("nosniff", response["X-Content-Type-Options"])
        self.assertIn("sandbox", response["Content-Security-Policy"])

    def test_html_attachment_is_never_previewed_inline(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener, \
             mock.patch(
                 "apps.postbox.views_mail.mime.extract_attachment",
                 return_value=("attack.html", "text/html", b"<script>x</script>"),
             ):
            connection = self._connection(opener)
            connection.fetch_raw.return_value = b"raw"
            response = self.api.get(
                "/api/postbox/messages/INBOX/4/attachments/2/preview/"
            )

        self.assertEqual(415, response.status_code)

    def test_custom_folder_rename_updates_rules_and_restore_provenance(self):
        rule = MailRule.objects.create(
            mailbox=self.alice,
            name="Client mail",
            enabled=True,
            field=MailRule.Field.FROM,
            match=MailRule.Match.CONTAINS,
            value="@client.test",
            action=MailRule.Action.MOVE,
            action_folder="Projects",
        )
        moved = summary(5, "Trash", message_id="<rename-provenance@example.com>")
        provenance = MessageMoveProvenance.objects.create(
            mailbox=self.alice,
            message_key=_message_provenance_key(moved),
            original_folder="Projects",
        )

        with mock.patch("apps.postbox.imap.open_mailbox") as opener, \
             mock.patch("apps.postbox.views_settings._sync_sieve") as sync:
            connection = self._connection(opener)
            connection.list_folders.return_value = [
                imap.FolderInfo("INBOX", role="inbox"),
                imap.FolderInfo("Projects"),
            ]

            response = self.api.patch(
                "/api/postbox/folders/Projects/",
                {"name": "Client Work"},
                format="json",
            )

        self.assertEqual(200, response.status_code)
        connection.rename_folder.assert_called_once_with("Projects", "Client Work")
        rule.refresh_from_db()
        provenance.refresh_from_db()
        self.assertEqual("Client Work", rule.action_folder)
        self.assertEqual("Client Work", provenance.original_folder)
        sync.assert_called_once_with(self.alice)

    def test_custom_folder_delete_moves_mail_to_inbox_and_disables_rules(self):
        rule = MailRule.objects.create(
            mailbox=self.alice,
            name="Project rule",
            enabled=True,
            field=MailRule.Field.SUBJECT,
            match=MailRule.Match.CONTAINS,
            value="Project",
            action=MailRule.Action.MOVE,
            action_folder="Projects",
        )
        moved = summary(8, "Trash", message_id="<delete-provenance@example.com>")
        provenance = MessageMoveProvenance.objects.create(
            mailbox=self.alice,
            message_key=_message_provenance_key(moved),
            original_folder="Projects",
        )

        with mock.patch("apps.postbox.imap.open_mailbox") as opener, \
             mock.patch("apps.postbox.views_settings._sync_sieve") as sync:
            connection = self._connection(opener)
            connection.list_folders.return_value = [
                imap.FolderInfo("INBOX", role="inbox"),
                imap.FolderInfo("Projects"),
            ]
            connection.select.return_value = imap.FolderInfo(
                name="Projects",
                uid_validity=7,
            )
            connection.search_uids.return_value = [4, 3]

            response = self.api.delete("/api/postbox/folders/Projects/")

        self.assertEqual(204, response.status_code)
        connection.move.assert_called_once_with([4, 3], "INBOX")
        connection.delete_folder.assert_called_once_with("Projects")
        rule.refresh_from_db()
        provenance.refresh_from_db()
        self.assertFalse(rule.enabled)
        self.assertEqual("Projects", rule.action_folder)
        self.assertEqual("INBOX", provenance.original_folder)
        sync.assert_called_once_with(self.alice)


    def test_trusted_remote_image_senders_can_be_listed_and_removed(self):
        RemoteImageSenderTrust.objects.create(
            mailbox=self.alice,
            sender="trusted@example.net",
        )
        RemoteImageSenderTrust.objects.create(
            mailbox=self.bob,
            sender="bob-only@example.net",
        )

        response = self.api.get(
            "/api/postbox/remote-images/trusted-senders/"
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            ["trusted@example.net"],
            [row["sender"] for row in response.json()["results"]],
        )

        response = self.api.delete(
            "/api/postbox/remote-images/trusted-senders/",
            {"sender": "trusted@example.net"},
            format="json",
        )
        self.assertEqual(204, response.status_code)
        self.assertFalse(
            RemoteImageSenderTrust.objects.filter(
                mailbox=self.alice,
                sender="trusted@example.net",
            ).exists()
        )
        self.assertTrue(
            RemoteImageSenderTrust.objects.filter(
                mailbox=self.bob,
                sender="bob-only@example.net",
            ).exists()
        )
