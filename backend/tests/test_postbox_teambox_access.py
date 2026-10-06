from datetime import timedelta
import uuid
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.mail_directory.models import AccessGrantKind, MailboxAccessGrant
from apps.mailboxes.models import Mailbox, MailboxKind, MailboxStatus
from apps.postbox import auth as postbox_auth
from apps.postbox.models import (
    Contact,
    PostBoxPushDevice,
    PostBoxSession,
    PushPlatform,
    PushProvider,
    PushTokenType,
    ScheduledMessage,
)
from apps.postbox import push
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
class PostBoxTeamBoxAccessTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@phase-d.test")
        self.tenant = make_tenant(self.owner, name="Phase D", slug="phase-d")
        self.domain = make_domain(self.tenant, "phase-d.test")

        self.alice = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="alice",
            full_name="Alice",
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        self.bob = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="bob",
            full_name="Bob",
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        self.team_box = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="support",
            full_name="Customer Support",
            kind=MailboxKind.TEAM_BOX,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )

    def client_for(self, mailbox=None):
        raw, session = PostBoxSession.issue(mailbox or self.alice)
        client = APIClient()
        client.cookies[postbox_auth.SESSION_COOKIE_NAME] = raw
        return client, session

    def grant(
        self,
        *,
        member=None,
        can_read=True,
        can_manage=False,
        can_send_as=False,
        can_send_on_behalf=False,
        target=None,
    ):
        return MailboxAccessGrant.objects.create(
            tenant=self.tenant,
            target_mailbox=target or self.team_box,
            grantee_mailbox=member or self.alice,
            grant_type=AccessGrantKind.TEAM_BOX,
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
                {"mailbox_id": str((target or self.team_box).id)},
                format="json",
            )

    def test_me_lists_authorized_teambox_but_keeps_personal_identity(self):
        self.grant(can_read=True)
        client, _ = self.client_for()

        response = client.get(ME)

        self.assertEqual(200, response.status_code)
        self.assertEqual(self.alice.email, response.data["mailbox"]["email"])
        self.assertEqual(
            self.alice.email,
            response.data["authenticated_mailbox"]["email"],
        )
        available = {
            row["mailbox"]["email"]: row
            for row in response.data["available_mailboxes"]
        }
        self.assertIn(self.alice.email, available)
        self.assertIn(self.team_box.email, available)
        self.assertFalse(available[self.team_box.email]["is_personal"])
        self.assertTrue(available[self.team_box.email]["permissions"]["can_read"])

    def test_switch_changes_active_mailbox_not_authenticated_identity(self):
        self.grant(can_read=True, can_manage=True)
        client, session = self.client_for()

        switched = self.switch(client)

        self.assertEqual(200, switched.status_code)
        self.assertEqual(self.team_box.email, switched.data["mailbox"]["email"])
        self.assertEqual(
            self.alice.email,
            switched.data["authenticated_mailbox"]["email"],
        )
        session.refresh_from_db()
        self.assertEqual(self.team_box.id, session.active_mailbox_id)

        me = client.get(ME)
        self.assertEqual(self.team_box.email, me.data["mailbox"]["email"])
        self.assertEqual(self.alice.email, me.data["authenticated_mailbox"]["email"])

    def test_ungranted_teambox_cannot_be_opened(self):
        client, session = self.client_for()

        response = self.switch(client)

        self.assertEqual(403, response.status_code)
        session.refresh_from_db()
        self.assertIsNone(session.active_mailbox_id)

    def test_cross_tenant_teambox_cannot_be_opened(self):
        other_owner = make_user("owner@other-phase-d.test")
        other_tenant = make_tenant(
            other_owner,
            name="Other Phase D",
            slug="other-phase-d",
        )
        other_domain = make_domain(other_tenant, "other-phase-d.test")
        other_team_box = Mailbox.objects.create(
            tenant=other_tenant,
            domain=other_domain,
            local_part="support",
            full_name="Other Support",
            kind=MailboxKind.TEAM_BOX,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        client, _ = self.client_for()

        response = self.switch(client, other_team_box)

        self.assertEqual(403, response.status_code)

    def test_read_only_member_can_read_but_cannot_mutate_shared_state(self):
        self.grant(can_read=True, can_manage=False)
        client, _ = self.client_for()
        self.assertEqual(200, self.switch(client).status_code)

        read = client.get("/api/postbox/contacts/")
        mutate = client.post(
            "/api/postbox/contacts/",
            {"name": "Shared Contact", "email": "shared@example.test"},
            format="json",
        )

        self.assertEqual(200, read.status_code)
        self.assertEqual(403, mutate.status_code)
        self.assertFalse(Contact.objects.filter(mailbox=self.team_box).exists())

    def test_manage_member_can_mutate_shared_mailbox_state(self):
        self.grant(can_read=True, can_manage=True)
        client, _ = self.client_for()
        self.assertEqual(200, self.switch(client).status_code)

        response = client.post(
            "/api/postbox/contacts/",
            {"name": "Shared Contact", "email": "shared@example.test"},
            format="json",
        )

        self.assertEqual(201, response.status_code, response.data)
        self.assertTrue(
            Contact.objects.filter(
                mailbox=self.team_box,
                email="shared@example.test",
            ).exists()
        )

    def test_send_only_member_has_identity_but_no_mailbox_read_or_drafts(self):
        self.grant(
            can_read=False,
            can_manage=False,
            can_send_as=True,
        )
        client, _ = self.client_for()
        self.assertEqual(200, self.switch(client).status_code)

        identities = client.get("/api/postbox/identities/")
        contacts = client.get("/api/postbox/contacts/")
        draft = client.post("/api/postbox/drafts/", {}, format="json")

        self.assertEqual(200, identities.status_code)
        self.assertEqual(
            [self.team_box.email],
            [row["address"] for row in identities.data["results"]],
        )
        self.assertEqual(403, contacts.status_code)
        self.assertEqual(403, draft.status_code)

    def test_immediate_teambox_send_authenticates_as_personal_member(self):
        self.grant(can_read=False, can_send_as=True)
        client, _ = self.client_for()
        self.assertEqual(200, self.switch(client).status_code)

        with mock.patch("apps.postbox.sending.submit") as submit, \
             mock.patch(
                 "apps.postbox.views_compose.SendView._file_in_sent",
                 return_value=True,
             ):
            response = client.post(
                "/api/postbox/compose/send/",
                {
                    "from_address": self.team_box.email,
                    "to": ["recipient@example.test"],
                    "subject": "Shared reply",
                    "text": "Hello",
                },
                format="json",
            )

        self.assertEqual(200, response.status_code, response.data)
        submit.assert_called_once()
        message = submit.call_args.args[0]
        self.assertEqual(self.team_box.email, submit.call_args.kwargs["envelope_from"])
        self.assertEqual(self.alice, submit.call_args.kwargs["mailbox"])
        self.assertEqual(self.team_box.email, message["From"].addresses[0].addr_spec)
        self.assertIsNone(message["Sender"])

    def test_send_on_behalf_adds_actor_sender_header(self):
        self.grant(
            can_read=False,
            can_send_as=False,
            can_send_on_behalf=True,
        )
        client, _ = self.client_for()
        self.assertEqual(200, self.switch(client).status_code)

        with mock.patch("apps.postbox.sending.submit") as submit, \
             mock.patch(
                 "apps.postbox.views_compose.SendView._file_in_sent",
                 return_value=True,
             ):
            response = client.post(
                "/api/postbox/compose/send/",
                {
                    "from_address": self.team_box.email,
                    "to": ["recipient@example.test"],
                    "subject": "On behalf",
                    "text": "Hello",
                },
                format="json",
            )

        self.assertEqual(200, response.status_code, response.data)
        message = submit.call_args.args[0]
        self.assertEqual(self.team_box.email, message["From"].addresses[0].addr_spec)
        self.assertEqual(self.alice.email, message["Sender"].addresses[0].addr_spec)
        self.assertEqual(self.alice, submit.call_args.kwargs["mailbox"])

    def test_send_only_member_cannot_schedule_inaccessible_message(self):
        self.grant(can_read=False, can_send_as=True)
        client, _ = self.client_for()
        self.assertEqual(200, self.switch(client).status_code)

        response = client.post(
            "/api/postbox/compose/send/",
            {
                "from_address": self.team_box.email,
                "to": ["recipient@example.test"],
                "subject": "Later",
                "text": "Hello",
                "send_at": (
                    timezone.now() + timedelta(hours=1)
                ).isoformat(),
            },
            format="json",
        )

        self.assertEqual(403, response.status_code)
        self.assertFalse(
            ScheduledMessage.objects.filter(mailbox=self.team_box).exists()
        )

    def test_read_and_send_member_schedule_records_personal_submission_identity(self):
        self.grant(can_read=True, can_send_as=True)
        client, _ = self.client_for()
        self.assertEqual(200, self.switch(client).status_code)

        connection = mock.MagicMock()
        connection.append.return_value = (9, 17)
        context = mock.MagicMock()
        context.__enter__.return_value = connection

        with mock.patch("apps.postbox.imap.open_mailbox", return_value=context):
            response = client.post(
                "/api/postbox/compose/send/",
                {
                    "from_address": self.team_box.email,
                    "to": ["recipient@example.test"],
                    "subject": "Later",
                    "text": "Hello",
                    "send_at": (
                        timezone.now() + timedelta(hours=1)
                    ).isoformat(),
                },
                format="json",
            )

        self.assertEqual(201, response.status_code, response.data)
        row = ScheduledMessage.objects.get(pk=response.data["id"])
        self.assertEqual(self.team_box, row.mailbox)
        self.assertEqual(self.alice, row.submission_mailbox)

    def test_revoked_grant_refuses_current_mail_action_then_recovers_personal_session(self):
        grant = self.grant(can_read=True, can_manage=True)
        client, session = self.client_for()
        self.assertEqual(200, self.switch(client).status_code)

        grant.delete()
        refused = client.get("/api/postbox/contacts/")

        self.assertEqual(403, refused.status_code)
        session.refresh_from_db()
        self.assertIsNone(session.active_mailbox_id)
        self.assertIsNone(session.revoked_at)

        recovered = client.get(ME)
        self.assertEqual(200, recovered.status_code)
        self.assertEqual(self.alice.email, recovered.data["mailbox"]["email"])
        self.assertEqual(
            self.alice.email,
            recovered.data["authenticated_mailbox"]["email"],
        )

    def test_teambox_push_registration_requires_live_read_grant(self):
        grant = self.grant(can_read=True, can_manage=False)
        _, session = self.client_for()
        device = PostBoxPushDevice.objects.create(
            mailbox=self.team_box,
            session=session,
            installation_id=uuid.uuid4(),
            platform=PushPlatform.ANDROID,
            provider=PushProvider.FCM,
            token_type=PushTokenType.REGISTRATION_TOKEN,
            token="test-device-token",
        )

        push.assert_mailbox_may_receive_push(self.team_box)
        self.assertEqual(
            [device.id],
            list(push.active_devices(self.team_box).values_list("id", flat=True)),
        )

        grant.can_read = False
        grant.can_send_as = True
        grant.save()

        self.assertEqual(
            [],
            list(push.active_devices(self.team_box).values_list("id", flat=True)),
        )

    def test_deleting_teambox_revokes_only_sessions_actively_using_it(self):
        self.grant(can_read=True, can_manage=True)
        active_client, active_session = self.client_for()
        self.assertEqual(200, self.switch(active_client).status_code)
        _, personal_session = self.client_for(self.alice)

        admin = auth_client(self.owner, self.tenant)
        with mock.patch(
            "apps.mail_engine.tasks.deprovision_mailbox_task.delay"
        ):
            response = admin.delete(f"/api/team-boxes/{self.team_box.id}/")

        self.assertEqual(204, response.status_code)
        active_session.refresh_from_db()
        personal_session.refresh_from_db()
        self.assertIsNotNone(active_session.revoked_at)
        self.assertIsNone(personal_session.revoked_at)
