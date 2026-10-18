"""SMTP/IMAP access staging must not disrupt existing client credentials."""
from django.test import TestCase

from apps.postbox.models import MailClientAccessPolicy
from tests.factories import make_domain, make_mailbox, make_tenant, make_user


class MailClientAccessFoundationTest(TestCase):
    def setUp(self):
        self.owner = make_user("mailclient-owner@example.test")
        self.tenant = make_tenant(self.owner, "Mail Client", "mail-client")
        self.domain = make_domain(self.tenant, "mailclient.example.test")
        self.mailbox = make_mailbox(self.tenant, self.domain)

    def test_legacy_mailbox_has_no_access_override(self):
        # No row is written as a side effect of the migration or login.
        self.assertFalse(MailClientAccessPolicy.objects.filter(mailbox=self.mailbox).exists())

    def test_new_policy_is_legacy_compatible(self):
        policy = MailClientAccessPolicy.objects.create(mailbox=self.mailbox)
        self.assertTrue(policy.smtp_imap_enabled)

    def test_access_record_is_mailbox_bound(self):
        from django.db import IntegrityError, transaction
        MailClientAccessPolicy.objects.create(mailbox=self.mailbox)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MailClientAccessPolicy.objects.create(mailbox=self.mailbox)
