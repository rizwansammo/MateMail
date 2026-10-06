import uuid
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.aliases.models import Alias
from apps.mail_directory.models import AccessGrantKind, MailboxAccessGrant
from apps.mailboxes.models import Mailbox, MailboxKind, MailboxStatus
from apps.postbox import auth as postbox_auth
from apps.postbox import push
from apps.postbox.models import (
    Contact,
    PostBoxPushDevice,
    PostBoxSession,
    PushPlatform,
    PushProvider,
    PushTokenType,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_user,
)


SWITCH_MAILBOX = "/api/postbox/auth/mailbox-switch/"
ME = "/api/postbox/auth/me/"


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class PostBoxDelegationAccessTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@phase-f.test")
        self.tenant = make_tenant(self.owner, name="Phase F", slug="phase-f")
        self.domain = make_domain(self.tenant, "phase-f.test")

        self.target = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="executive",
            full_name="Executive",
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        self.delegate = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="assistant",
            full_name="Assistant",
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        self.other = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="other",
            full_name="Other",
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )

    def client_for_delegate(self):
        raw, session = PostBoxSession.issue(self.delegate)
        client = APIClient()
        client.cookies[postbox_auth.SESSION_COOKIE_NAME] = raw
        return client, session

    def grant(
        self,
        *,
        can_read=True,
        can_manage=False,
        can_send_as=False,
        can_send_on_behalf=False,
    ):
        return MailboxAccessGrant.objects.create(
            tenant=self.tenant,
            target_mailbox=self.target,
            grantee_mailbox=self.delegate,
            grant_type=AccessGrantKind.DELEGATION,
            can_read=can_read,
            can_manage=can_manage,
            can_send_as=can_send_as,
            can_send_on_behalf=can_send_on_behalf,
            active=True,
        )

    def switch(self, client, target=None):
        with mock.patch("apps.postbox.imap.open_mailbox"):
            return client.post(
                SWITCH_MAILBOX,
                {"mailbox_id": str((target or self.target).id)},
                format="json",
            )

    def test_me_lists_delegation_separately_from_personal_and_teambox_access(self):
        self.grant(can_read=True)
        client, _ = self.client_for_delegate()

        response = client.get(ME)

        self.assertEqual(200, response.status_code)
        available = {
            row["mailbox"]["email"]: row
            for row in response.data["available_mailboxes"]
        }
        self.assertEqual("personal", available[self.delegate.email]["access_type"])
        self.assertEqual("delegation", available[self.target.email]["access_type"])
        self.assertTrue(available[self.target.email]["permissions"]["can_read"])

    def test_switch_keeps_delegate_as_authenticated_identity(self):
        self.grant(can_read=True, can_manage=True)
        client, session = self.client_for_delegate()

        response = self.switch(client)

        self.assertEqual(200, response.status_code, response.data)
        self.assertEqual(self.target.email, response.data["mailbox"]["email"])
        self.assertEqual(
            self.delegate.email,
            response.data["authenticated_mailbox"]["email"],
        )
        session.refresh_from_db()
        self.assertEqual(self.target.id, session.active_mailbox_id)

    def test_personal_mailbox_without_delegation_cannot_be_opened(self):
        client, session = self.client_for_delegate()

        response = self.switch(client)

        self.assertEqual(403, response.status_code)
        session.refresh_from_db()
        self.assertIsNone(session.active_mailbox_id)

    def test_read_only_delegate_can_read_but_not_manage_target_mailbox(self):
        self.grant(can_read=True, can_manage=False)
        client, _ = self.client_for_delegate()
        self.assertEqual(200, self.switch(client).status_code)

        read = client.get("/api/postbox/contacts/")
        mutate = client.post(
            "/api/postbox/contacts/",
            {"name": "Executive Contact", "email": "contact@example.test"},
            format="json",
        )

        self.assertEqual(200, read.status_code)
        self.assertEqual(403, mutate.status_code)
        self.assertFalse(
            Contact.objects.filter(mailbox=self.target).exists()
        )

    def test_manage_delegate_mutates_target_not_delegate_mailbox(self):
        self.grant(can_read=True, can_manage=True)
        client, _ = self.client_for_delegate()
        self.assertEqual(200, self.switch(client).status_code)

        response = client.post(
            "/api/postbox/contacts/",
            {"name": "Executive Contact", "email": "contact@example.test"},
            format="json",
        )

        self.assertEqual(201, response.status_code, response.data)
        self.assertTrue(
            Contact.objects.filter(
                mailbox=self.target,
                email="contact@example.test",
            ).exists()
        )
        self.assertFalse(
            Contact.objects.filter(
                mailbox=self.delegate,
                email="contact@example.test",
            ).exists()
        )

    def test_send_as_delegated_mailbox_authenticates_smtp_as_delegate(self):
        self.grant(can_read=False, can_send_as=True)
        client, _ = self.client_for_delegate()
        self.assertEqual(200, self.switch(client).status_code)

        with mock.patch("apps.postbox.sending.submit") as submit, \
             mock.patch(
                 "apps.postbox.views_compose.SendView._file_in_sent",
                 return_value=True,
             ):
            response = client.post(
                "/api/postbox/compose/send/",
                {
                    "from_address": self.target.email,
                    "to": ["recipient@example.test"],
                    "subject": "Delegated send",
                    "text": "Hello",
                },
                format="json",
            )

        self.assertEqual(200, response.status_code, response.data)
        message = submit.call_args.args[0]
        self.assertEqual(self.delegate, submit.call_args.kwargs["mailbox"])
        self.assertEqual(self.target.email, submit.call_args.kwargs["envelope_from"])
        self.assertEqual(self.target.email, message["From"].addresses[0].addr_spec)
        self.assertIsNone(message["Sender"])

    def test_send_on_behalf_marks_delegate_in_sender_header(self):
        self.grant(can_read=False, can_send_on_behalf=True)
        client, _ = self.client_for_delegate()
        self.assertEqual(200, self.switch(client).status_code)

        with mock.patch("apps.postbox.sending.submit") as submit, \
             mock.patch(
                 "apps.postbox.views_compose.SendView._file_in_sent",
                 return_value=True,
             ):
            response = client.post(
                "/api/postbox/compose/send/",
                {
                    "from_address": self.target.email,
                    "to": ["recipient@example.test"],
                    "subject": "On behalf",
                    "text": "Hello",
                },
                format="json",
            )

        self.assertEqual(200, response.status_code, response.data)
        message = submit.call_args.args[0]
        self.assertEqual(self.target.email, message["From"].addresses[0].addr_spec)
        self.assertEqual(
            self.delegate.email,
            message["Sender"].addresses[0].addr_spec,
        )

    def test_target_alias_is_available_to_delegate_with_send_as(self):
        Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="executive-office@phase-f.test",
            destination_mailbox=self.target,
        )
        self.grant(can_read=False, can_send_as=True)
        client, _ = self.client_for_delegate()
        self.assertEqual(200, self.switch(client).status_code)

        response = client.get("/api/postbox/identities/")

        self.assertEqual(200, response.status_code)
        addresses = {
            row["address"] for row in response.data["results"]
        }
        self.assertEqual(
            {self.target.email, "executive-office@phase-f.test"},
            addresses,
        )

    def test_revoked_delegation_refuses_current_action_then_recovers_delegate_mailbox(self):
        grant = self.grant(can_read=True, can_manage=True)
        client, session = self.client_for_delegate()
        self.assertEqual(200, self.switch(client).status_code)

        grant.delete()
        refused = client.get("/api/postbox/contacts/")

        self.assertEqual(403, refused.status_code)
        session.refresh_from_db()
        self.assertIsNone(session.active_mailbox_id)
        self.assertIsNone(session.revoked_at)

        recovered = client.get(ME)
        self.assertEqual(200, recovered.status_code)
        self.assertEqual(self.delegate.email, recovered.data["mailbox"]["email"])

    def test_push_visibility_stops_when_delegation_read_is_removed(self):
        grant = self.grant(can_read=True)
        _, session = self.client_for_delegate()
        device = PostBoxPushDevice.objects.create(
            mailbox=self.target,
            session=session,
            installation_id=uuid.uuid4(),
            platform=PushPlatform.ANDROID,
            provider=PushProvider.FCM,
            token_type=PushTokenType.REGISTRATION_TOKEN,
            token="delegated-device-token",
        )

        self.assertEqual(
            [device.id],
            list(
                push.active_devices(self.target)
                .values_list("id", flat=True)
            ),
        )

        grant.can_read = False
        grant.can_send_as = True
        grant.save()

        self.assertEqual(
            [],
            list(
                push.active_devices(self.target)
                .values_list("id", flat=True)
            ),
        )

    def test_deleting_delegated_target_revokes_active_delegate_session(self):
        self.grant(can_read=True, can_manage=True)
        client, session = self.client_for_delegate()
        self.assertEqual(200, self.switch(client).status_code)

        admin = auth_client(self.owner, self.tenant)
        with mock.patch(
            "apps.mail_engine.tasks.deprovision_mailbox_task.delay"
        ):
            response = admin.delete(f"/api/mailboxes/{self.target.id}/")

        self.assertEqual(204, response.status_code)
        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)
