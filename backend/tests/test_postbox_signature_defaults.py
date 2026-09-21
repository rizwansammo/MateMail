from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox.models import MailSignature
from tests.factories import FAST_PASSWORD_HASHERS, disable_throttling, make_tenant, make_user


LOGIN = "/api/postbox/auth/login/"
LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postbox-signature-default-tests",
    }
}


def make_mailbox(tenant, domain, local_part):
    return Mailbox.objects.create(
        tenant=tenant,
        domain=domain,
        local_part=local_part,
        email=f"{local_part}@{domain.domain}",
        full_name=local_part.title(),
        status=MailboxStatus.ACTIVE,
        mail_engine_provisioned=True,
    )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PostBoxSignatureDefaultsTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        disable_throttling(self)

        owner = make_user("owner@acme.test")
        self.tenant = make_tenant(owner, name="Acme", slug="acme")
        self.domain = Domain.objects.create(
            tenant=self.tenant,
            domain="acme.test",
            status="active",
            ownership_status="verified",
            mail_engine_provisioned=True,
        )
        self.alice = make_mailbox(self.tenant, self.domain, "alice")
        self.bob = make_mailbox(self.tenant, self.domain, "bob")

    def login_as(self, mailbox):
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            client = APIClient()
            response = client.post(
                LOGIN,
                {"email": mailbox.email, "password": "x"},
                format="json",
            )
            self.assertEqual(200, response.status_code)
            return client

    def test_enabling_new_default_disables_the_previous_one(self):
        client = self.login_as(self.alice)
        first = client.post(
            "/api/postbox/signatures/",
            {
                "name": "First",
                "text": "First text",
                "html": "<p>First</p>",
                "use_for_new": True,
            },
            format="json",
        )
        second = client.post(
            "/api/postbox/signatures/",
            {
                "name": "Second",
                "text": "Second text",
                "html": "<p>Second</p>",
                "use_for_new": True,
            },
            format="json",
        )

        self.assertEqual(201, first.status_code)
        self.assertEqual(201, second.status_code)

        first_row = MailSignature.objects.get(pk=first.data["id"])
        second_row = MailSignature.objects.get(pk=second.data["id"])
        self.assertFalse(first_row.use_for_new)
        self.assertTrue(second_row.use_for_new)
        self.assertEqual("First text", first_row.text)
        self.assertEqual("<p>First</p>", first_row.html)

    def test_enabling_reply_default_disables_the_previous_one(self):
        first = MailSignature.objects.create(
            mailbox=self.alice,
            name="First",
            use_for_replies=True,
        )
        second = MailSignature.objects.create(
            mailbox=self.alice,
            name="Second",
        )

        response = self.login_as(self.alice).patch(
            f"/api/postbox/signatures/{second.id}/",
            {"use_for_replies": True},
            format="json",
        )

        self.assertEqual(200, response.status_code)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.use_for_replies)
        self.assertTrue(second.use_for_replies)

    def test_new_and_reply_defaults_are_independent(self):
        new_default = MailSignature.objects.create(
            mailbox=self.alice,
            name="New",
            use_for_new=True,
        )
        reply_default = MailSignature.objects.create(
            mailbox=self.alice,
            name="Reply",
            use_for_replies=True,
        )

        response = self.login_as(self.alice).patch(
            f"/api/postbox/signatures/{reply_default.id}/",
            {"use_for_new": True},
            format="json",
        )

        self.assertEqual(200, response.status_code)
        new_default.refresh_from_db()
        reply_default.refresh_from_db()
        self.assertFalse(new_default.use_for_new)
        self.assertTrue(reply_default.use_for_new)
        self.assertTrue(reply_default.use_for_replies)

    def test_defaults_are_scoped_per_mailbox(self):
        alice = MailSignature.objects.create(
            mailbox=self.alice,
            name="Alice",
            use_for_new=True,
            use_for_replies=True,
        )
        bob = MailSignature.objects.create(
            mailbox=self.bob,
            name="Bob",
            use_for_new=True,
            use_for_replies=True,
        )

        self.assertTrue(alice.use_for_new)
        self.assertTrue(alice.use_for_replies)
        self.assertTrue(bob.use_for_new)
        self.assertTrue(bob.use_for_replies)

    def test_database_rejects_two_direct_new_defaults_for_one_mailbox(self):
        MailSignature.objects.create(
            mailbox=self.alice,
            name="First",
            use_for_new=True,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MailSignature.objects.create(
                    mailbox=self.alice,
                    name="Second",
                    use_for_new=True,
                )

    def test_database_rejects_two_direct_reply_defaults_for_one_mailbox(self):
        MailSignature.objects.create(
            mailbox=self.alice,
            name="First",
            use_for_replies=True,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MailSignature.objects.create(
                    mailbox=self.alice,
                    name="Second",
                    use_for_replies=True,
                )
