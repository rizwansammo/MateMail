"""Regression tests for persistent, mailbox-private PostBox folder/label colors."""
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox
from apps.postbox.models import FolderAppearance, MailLabel
from tests.factories import FAST_PASSWORD_HASHERS, disable_throttling, make_tenant, make_user


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class PostBoxAppearanceTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        owner = make_user("appearance-owner@example.test")
        tenant = make_tenant(owner, name="Appearance", slug="appearance")
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
        self.client = self.login(self.alice)
        self.other = self.login(self.bob)

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
    def fake_imap(name="Finance"):
        fake = mock.MagicMock()
        fake.list_folders.return_value = [
            SimpleNamespace(name="INBOX", role="inbox", selectable=True),
            SimpleNamespace(name=name, role="", selectable=True),
        ]
        fake.folder_counts.return_value = (2, 1)
        fake.search_uids.return_value = []
        return fake

    def test_label_create_color_patch_and_badge_color_are_mailbox_private(self):
        created = self.client.post("/api/postbox/labels/", {
            "name": "Invoices", "color": "#F97316",
        }, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data["id"]
        self.assertEqual(created.data["color"], "#f97316")
        self.assertEqual(MailLabel.objects.get(pk=pk).color, "#f97316")

        listed = self.client.get("/api/postbox/labels/")
        self.assertEqual(listed.data["results"][0]["color"], "#f97316")
        self.assertEqual(self.other.get("/api/postbox/labels/").data["results"], [])
        self.assertEqual(self.other.patch(
            "/api/postbox/labels/" + pk + "/", {"color": "#dc2626"},
            format="json",
        ).status_code, 404)

        patched = self.client.patch(
            "/api/postbox/labels/" + pk + "/", {"color": "#059669"},
            format="json",
        )
        self.assertEqual(patched.status_code, 200, patched.data)
        self.assertEqual(patched.data["name"], "Invoices")
        self.assertEqual(patched.data["color"], "#059669")
        self.assertEqual(MailLabel.objects.get(pk=pk).color, "#059669")

        renamed = self.client.patch(
            "/api/postbox/labels/" + pk + "/", {"name": "Finance"},
            format="json",
        )
        self.assertEqual(renamed.status_code, 200, renamed.data)
        self.assertEqual(renamed.data["color"], "#059669")
        self.assertEqual(self.client.delete(
            "/api/postbox/labels/" + pk + "/",
        ).status_code, 204)
        self.assertFalse(MailLabel.objects.filter(pk=pk).exists())

    def test_bad_colors_rejected_without_mutating_labels(self):
        label = MailLabel.objects.create(
            mailbox=self.alice, name="Finance", color="#2563eb",
        )
        for color in ("red", "#abc", "#fff;display:none", "var(--x)", "", None, 8):
            with self.subTest(color=color):
                result = self.client.patch(
                    "/api/postbox/labels/" + str(label.pk) + "/",
                    {"color": color}, format="json",
                )
                self.assertEqual(result.status_code, 400)
                label.refresh_from_db()
                self.assertEqual(label.color, "#2563eb")
        self.assertEqual(self.client.post(
            "/api/postbox/labels/", {"name": "Invalid", "color": "red"},
            format="json",
        ).status_code, 400)
        self.assertFalse(MailLabel.objects.filter(
            mailbox=self.alice, name="Invalid",
        ).exists())

    def test_folder_color_create_read_and_color_only_update(self):
        fake = self.fake_imap()
        with mock.patch("apps.postbox.views_mail.imap.open_mailbox") as opened:
            opened.return_value.__enter__.return_value = fake
            created = self.client.post(
                "/api/postbox/folders/", {"name": "Finance", "color": "#DB2777"},
                format="json",
            )
            self.assertEqual(created.status_code, 201, created.data)
            self.assertEqual(created.data["color"], "#db2777")
            self.assertEqual(FolderAppearance.objects.get(
                mailbox=self.alice, name="Finance",
            ).color, "#db2777")

            listed = self.client.get("/api/postbox/folders/")
            found = next(item for item in listed.data["results"]
                         if item["name"] == "Finance")
            self.assertEqual(found["color"], "#db2777")
            inbox = next(item for item in listed.data["results"]
                         if item["name"] == "INBOX")
            self.assertIsNone(inbox["color"])

            color_only = self.client.patch(
                "/api/postbox/folders/Finance/", {"color": "#0d9488"},
                format="json",
            )
            self.assertEqual(color_only.status_code, 200, color_only.data)
            self.assertEqual(color_only.data["name"], "Finance")
            self.assertEqual(color_only.data["color"], "#0d9488")
            fake.rename_folder.assert_not_called()

            # No mailbox A appearance can affect mailbox B's sidebar.
            other = self.other.get("/api/postbox/folders/")
            other_finance = next(item for item in other.data["results"]
                                 if item["name"] == "Finance")
            self.assertEqual(other_finance["color"], "#2563eb")

            rejected = self.client.patch(
                "/api/postbox/folders/Finance/", {"color": "javascript:alert(1)"},
                format="json",
            )
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(FolderAppearance.objects.get(
                mailbox=self.alice, name="Finance",
            ).color, "#0d9488")
            self.assertEqual(self.client.patch(
                "/api/postbox/folders/INBOX/", {"color": "#dc2626"},
                format="json",
            ).status_code, 400)

    def test_folder_color_survives_rename_and_is_cleaned_on_safe_delete(self):
        appearance = FolderAppearance.objects.create(
            mailbox=self.alice, name="Finance", color="#9333ea",
        )
        fake = self.fake_imap()
        with mock.patch("apps.postbox.views_mail.imap.open_mailbox") as opened, \
             mock.patch("apps.postbox.views_settings._sync_sieve"):
            opened.return_value.__enter__.return_value = fake
            changed = self.client.patch(
                "/api/postbox/folders/Finance/", {"name": "Invoices"},
                format="json",
            )
            self.assertEqual(changed.status_code, 200, changed.data)
            appearance.refresh_from_db()
            self.assertEqual(appearance.name, "Invoices")
            self.assertEqual(appearance.color, "#9333ea")
            fake.rename_folder.assert_called_once_with("Finance", "Invoices")

            fake.list_folders.return_value = [
                SimpleNamespace(name="Invoices", role="", selectable=True),
            ]
            deleted = self.client.delete("/api/postbox/folders/Invoices/")
            self.assertEqual(deleted.status_code, 204)
            self.assertFalse(FolderAppearance.objects.filter(
                mailbox=self.alice, name="Invoices",
            ).exists())
            fake.delete_folder.assert_called_once_with("Invoices")
