"""Remote images stay blocked unless the mailbox explicitly allows them."""

from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import imap, mime
from apps.postbox.models import (
    PostBoxPreference,
    RemoteImageSenderTrust,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    disable_throttling,
    make_tenant,
    make_user,
)


LOGIN = "/api/postbox/auth/login/"
REMOTE_URL = "https://images.example.test/open.gif"
SENDER = "success@e.rocketreach.co"
LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postbox-remote-image-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class RemoteImageSenderTrustTest(TestCase):
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

        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            self.api = APIClient()
            response = self.api.post(
                LOGIN,
                {"email": "alice@example.com", "password": "x"},
                format="json",
            )
            self.assertEqual(200, response.status_code)

    @staticmethod
    def _raw(sender=SENDER):
        return mime.build_message(
            from_address=sender,
            from_name="RocketReach Success",
            to=["alice@example.com"],
            subject="Remote images",
            text="Fallback",
            html=f'<p>Hello</p><img src="{REMOTE_URL}" alt="pixel">',
        ).as_bytes()

    @staticmethod
    def _connection(opener, raw):
        connection = opener.return_value.__enter__.return_value
        connection.select.return_value = imap.FolderInfo(
            name="INBOX",
            role="inbox",
            uid_validity=7,
        )
        connection.fetch_raw.return_value = raw
        return connection

    def _detail(self, raw, query=""):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            self._connection(opener, raw)
            response = self.api.get(
                f"/api/postbox/messages/INBOX/41/{query}"
            )
        self.assertEqual(200, response.status_code)
        return response.json()

    def _trust(self, raw):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            self._connection(opener, raw)
            return self.api.post(
                "/api/postbox/messages/INBOX/41/remote-images/trust/"
                "?uid_validity=7",
                {},
                format="json",
            )

    def test_remote_images_are_blocked_by_default(self):
        detail = self._detail(self._raw())
        self.assertTrue(detail["remote_images_blocked"])
        self.assertNotIn(REMOTE_URL, detail["html"])
        self.assertNotIn('src=""', detail["html"])

    def test_display_images_is_only_for_the_current_request(self):
        shown = self._detail(self._raw(), "?remote_images=true")
        self.assertFalse(shown["remote_images_blocked"])
        self.assertIn(REMOTE_URL, shown["html"])

        reloaded = self._detail(self._raw())
        self.assertTrue(reloaded["remote_images_blocked"])
        self.assertNotIn(REMOTE_URL, reloaded["html"])

    def test_always_display_from_sender_persists_across_reloads(self):
        response = self._trust(self._raw("Success@E.RocketReach.co"))
        self.assertEqual(201, response.status_code)
        self.assertEqual(SENDER, response.json()["sender"])
        self.assertTrue(response.json()["trusted"])
        self.assertTrue(
            RemoteImageSenderTrust.objects.filter(
                mailbox=self.alice,
                sender=SENDER,
            ).exists()
        )

        first = self._detail(self._raw())
        second = self._detail(self._raw())
        for detail in (first, second):
            self.assertFalse(detail["remote_images_blocked"])
            self.assertIn(REMOTE_URL, detail["html"])

    def test_sender_preference_is_exact_and_mailbox_scoped(self):
        RemoteImageSenderTrust.objects.create(mailbox=self.bob, sender=SENDER)

        alice_view = self._detail(self._raw())
        self.assertTrue(alice_view["remote_images_blocked"])

        response = self._trust(self._raw())
        self.assertEqual(201, response.status_code)

        other = self._detail(self._raw("other@example.net"))
        self.assertTrue(other["remote_images_blocked"])
        self.assertNotIn(REMOTE_URL, other["html"])

    def test_global_preference_loads_remote_images_for_every_sender(self):
        preference, _ = PostBoxPreference.objects.get_or_create(mailbox=self.alice)
        preference.load_remote_images = True
        preference.save(update_fields=["load_remote_images"])

        detail = self._detail(self._raw("other@example.net"))
        self.assertFalse(detail["remote_images_blocked"])
        self.assertIn(REMOTE_URL, detail["html"])
