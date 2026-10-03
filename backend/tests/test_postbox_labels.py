"""Regression coverage for mailbox-private virtual labels."""
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox
from apps.postbox.models import MailLabel, MessageLabel
from apps.postbox.views_labels import labels_for_summaries
from tests.factories import FAST_PASSWORD_HASHERS, disable_throttling, make_tenant, make_user


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class VirtualLabelTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        owner = make_user("label-owner@example.test")
        tenant = make_tenant(owner, name="Labels", slug="label-space")
        domain = Domain.objects.create(
            tenant=tenant, domain="example.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.alice = Mailbox.objects.create(
            tenant=tenant, domain=domain, local_part="alice",
            email="alice@example.test", full_name="Alice",
            status="active", mail_engine_provisioned=True,
        )
        self.bob = Mailbox.objects.create(
            tenant=tenant, domain=domain, local_part="bob",
            email="bob@example.test", full_name="Bob",
            status="active", mail_engine_provisioned=True,
        )
        self.alice_client = self.login(self.alice)
        self.bob_client = self.login(self.bob)

    def login(self, mailbox):
        client = APIClient()
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            result = client.post("/api/postbox/auth/login/", {
                "email": mailbox.email, "password": "secret",
            }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        return client

    @staticmethod
    def message(folder="INBOX"):
        return SimpleNamespace(
            uid=7, uid_validity=42, folder=folder,
            message_id="<unique-invoice@example.test>", subject="Invoice",
            from_address="accounts@example.test", from_name="Accounts",
            date="Fri, 02 Oct 2026 20:00:00 +0000", size=1042,
            to=[], cc=[], seen=False, flagged=False, answered=False,
            draft=False, has_attachments=False, in_reply_to="",
            thread_references=[],
        )

    def test_create_is_scoped_case_insensitive_and_name_only(self):
        created = self.alice_client.post(
            "/api/postbox/labels/", {"name": "Finance"}, format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["count"], 0)
        duplicate = self.alice_client.post(
            "/api/postbox/labels/", {"name": "finance"}, format="json",
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            self.bob_client.get("/api/postbox/labels/").data["results"], [],
        )
        self.assertEqual(
            self.bob_client.delete("/api/postbox/labels/" + created.data["id"] + "/").status_code,
            404,
        )
        # Identical names in different mailboxes are not collisions.
        self.assertEqual(
            self.bob_client.post(
                "/api/postbox/labels/", {"name": "Finance"}, format="json",
            ).status_code,
            201,
        )

    def test_assign_multiple_labels_without_an_imap_copy_and_survive_move(self):
        first = MailLabel.objects.create(mailbox=self.alice, name="Finance")
        second = MailLabel.objects.create(mailbox=self.alice, name="Important")
        fake = mock.MagicMock()
        fake.select.return_value = SimpleNamespace(uid_validity=42)
        fake.fetch_summaries.return_value = [self.message()]
        with mock.patch("apps.postbox.views_labels.imap.open_mailbox") as open_mailbox:
            open_mailbox.return_value.__enter__.return_value = fake
            for label in (first, second):
                result = self.alice_client.post(
                    "/api/postbox/labels/assign/",
                    {"label_id": str(label.pk), "folder": "INBOX",
                     "uids": [7], "uid_validity": 42, "remove": False},
                    format="json",
                )
                self.assertEqual(result.status_code, 200, result.data)
            # Idempotent assignment cannot duplicate either virtual label.
            self.assertEqual(self.alice_client.post(
                "/api/postbox/labels/assign/",
                {"label_id": str(first.pk), "folder": "INBOX",
                 "uids": [7], "uid_validity": 42, "remove": False}, format="json",
            ).status_code, 200)
        self.assertEqual(MessageLabel.objects.count(), 2)
        self.assertEqual(
            {entry["name"] for entries in labels_for_summaries(
                self.alice, [self.message(folder="Finance")],
            ).values() for entry in entries},
            {"Finance", "Important"},
        )
        fake.move.assert_not_called()
        fake.copy.assert_not_called()

        # Renaming or deleting a label never touches the actual IMAP message.
        self.assertEqual(self.alice_client.patch(
            "/api/postbox/labels/" + str(first.pk) + "/",
            {"name": "Bills"}, format="json",
        ).status_code, 200)
        self.assertEqual(self.alice_client.delete(
            "/api/postbox/labels/" + str(first.pk) + "/",
        ).status_code, 204)
        self.assertEqual(MessageLabel.objects.count(), 1)

    def test_stale_or_missing_uid_does_not_label_unrelated_mail(self):
        label = MailLabel.objects.create(mailbox=self.alice, name="Finance")
        fake = mock.MagicMock()
        fake.select.return_value = SimpleNamespace(uid_validity=99)
        with mock.patch("apps.postbox.views_labels.imap.open_mailbox") as opened:
            opened.return_value.__enter__.return_value = fake
            result = self.alice_client.post("/api/postbox/labels/assign/", {
                "label_id": str(label.id), "folder": "INBOX",
                "uids": [7], "uid_validity": 42,
            }, format="json")
            self.assertEqual(result.status_code, 409)
            fake.select.return_value = SimpleNamespace(uid_validity=42)
            fake.fetch_summaries.return_value = []
            result = self.alice_client.post("/api/postbox/labels/assign/", {
                "label_id": str(label.id), "folder": "INBOX",
                "uids": [7], "uid_validity": 42,
            }, format="json")
            self.assertEqual(result.status_code, 409)
        self.assertEqual(MessageLabel.objects.count(), 0)

    def test_label_view_uses_live_imap_and_keeps_original_folder(self):
        label = MailLabel.objects.create(mailbox=self.alice, name="Finance")
        summary = self.message(folder="Archive")
        from apps.postbox.views_mail import _message_provenance_key
        MessageLabel.objects.create(
            label=label, message_key=_message_provenance_key(summary),
        )
        fake = mock.MagicMock()
        fake.list_folders.return_value = [
            SimpleNamespace(name="Archive", role="archive", selectable=True)
        ]
        fake.search_uids.return_value = [7]
        fake.fetch_summaries.return_value = [summary]
        with mock.patch("apps.postbox.views_labels.imap.open_mailbox") as opened:
            opened.return_value.__enter__.return_value = fake
            response = self.alice_client.get(
                "/api/postbox/labels/" + str(label.pk) + "/messages/",
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["folder"], "Archive")
        self.assertEqual(response.data["results"][0]["labels"][0]["name"], "Finance")
