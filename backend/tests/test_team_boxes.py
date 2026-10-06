from unittest import mock

from django.test import TestCase, override_settings

from apps.aliases.models import Alias
from apps.mail_directory.models import AccessGrantKind, MailboxAccessGrant
from apps.mail_engine.dto import MailboxSpec
from apps.mailboxes.models import Mailbox, MailboxKind
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_mailbox,
    make_tenant,
    make_user,
)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class TeamBoxApiTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@teambox.test")
        self.tenant = make_tenant(self.owner, name="TeamBox Co", slug="teambox-co")
        self.domain = make_domain(self.tenant, "teambox.test")
        self.alice = make_mailbox(self.tenant, self.domain, "alice")
        self.bob = make_mailbox(self.tenant, self.domain, "bob")
        self.client = auth_client(self.owner, self.tenant)

    def create_team_box(self, local_part="support"):
        response = self.client.post(
            "/api/team-boxes/",
            {
                "local_part": local_part,
                "domain_id": str(self.domain.id),
                "display_name": "Customer Support",
                "quota_mb": 1024,
            },
            format="json",
        )
        self.assertEqual(201, response.status_code, response.data)
        return Mailbox.objects.get(pk=response.data["id"])

    def test_create_team_box_is_passwordless_and_distinct_from_personal_mailbox(self):
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox"
        ) as ensure:
            team_box = self.create_team_box()

        self.assertEqual(MailboxKind.TEAM_BOX, team_box.kind)
        ensure.assert_called_once()
        (spec, password), _ = ensure.call_args
        self.assertIsInstance(spec, MailboxSpec)
        self.assertFalse(spec.login_enabled)
        self.assertEqual((), spec.authorized_senders)
        self.assertEqual("", password)

    def test_personal_mailbox_password_endpoint_does_not_accept_a_team_box(self):
        team_box = self.create_team_box()
        response = self.client.post(
            f"/api/mailboxes/{team_box.id}/reprovision/",
            {"password": "Should-Not-Apply-9"},
            format="json",
        )
        self.assertEqual(404, response.status_code)

    def test_member_send_as_permission_reaches_engine_authorization(self):
        team_box = self.create_team_box()
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox"
        ) as ensure:
            response = self.client.post(
                f"/api/team-boxes/{team_box.id}/members/",
                {
                    "mailbox_id": str(self.alice.id),
                    "can_read": True,
                    "can_manage": True,
                    "can_send_as": True,
                    "can_send_on_behalf": False,
                },
                format="json",
            )

        self.assertEqual(201, response.status_code, response.data)
        grant = MailboxAccessGrant.objects.get(
            target_mailbox=team_box,
            grantee_mailbox=self.alice,
        )
        self.assertEqual(AccessGrantKind.TEAM_BOX, grant.grant_type)
        (spec, password), _ = ensure.call_args
        self.assertEqual((self.alice.email,), spec.authorized_senders)
        self.assertFalse(spec.login_enabled)
        self.assertEqual("", password)

    def test_read_only_member_does_not_gain_smtp_sender_authorization(self):
        team_box = self.create_team_box()
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox"
        ) as ensure:
            response = self.client.post(
                f"/api/team-boxes/{team_box.id}/members/",
                {
                    "mailbox_id": str(self.alice.id),
                    "can_read": True,
                    "can_manage": False,
                    "can_send_as": False,
                    "can_send_on_behalf": False,
                },
                format="json",
            )
        self.assertEqual(201, response.status_code)
        (spec, _), _ = ensure.call_args
        self.assertEqual((), spec.authorized_senders)

    def test_send_on_behalf_also_requires_engine_sender_authorization(self):
        team_box = self.create_team_box()
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox"
        ) as ensure:
            response = self.client.post(
                f"/api/team-boxes/{team_box.id}/members/",
                {
                    "mailbox_id": str(self.bob.id),
                    "can_read": False,
                    "can_manage": False,
                    "can_send_as": False,
                    "can_send_on_behalf": True,
                },
                format="json",
            )
        self.assertEqual(201, response.status_code)
        (spec, _), _ = ensure.call_args
        self.assertEqual((self.bob.email,), spec.authorized_senders)

    def test_cross_tenant_mailbox_cannot_be_added_as_member(self):
        other_owner = make_user("other@teambox.test")
        other_tenant = make_tenant(other_owner, name="Other", slug="other-team-box")
        other_domain = make_domain(other_tenant, "other-teambox.test")
        outsider = make_mailbox(other_tenant, other_domain, "outsider")
        team_box = self.create_team_box()

        response = self.client.post(
            f"/api/team-boxes/{team_box.id}/members/",
            {
                "mailbox_id": str(outsider.id),
                "can_read": True,
            },
            format="json",
        )
        self.assertEqual(400, response.status_code)
        self.assertFalse(
            MailboxAccessGrant.objects.filter(
                target_mailbox=team_box,
                grantee_mailbox=outsider,
            ).exists()
        )

    def test_manage_permission_requires_read(self):
        team_box = self.create_team_box()
        response = self.client.post(
            f"/api/team-boxes/{team_box.id}/members/",
            {
                "mailbox_id": str(self.alice.id),
                "can_read": False,
                "can_manage": True,
                "can_send_as": False,
                "can_send_on_behalf": False,
            },
            format="json",
        )
        self.assertEqual(400, response.status_code)

    def test_member_permissions_can_be_updated_and_sender_right_removed(self):
        team_box = self.create_team_box()
        created = self.client.post(
            f"/api/team-boxes/{team_box.id}/members/",
            {
                "mailbox_id": str(self.alice.id),
                "can_read": True,
                "can_manage": True,
                "can_send_as": True,
            },
            format="json",
        )
        member_id = created.data["id"]

        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox"
        ) as ensure:
            response = self.client.patch(
                f"/api/team-boxes/{team_box.id}/members/{member_id}/",
                {
                    "can_read": True,
                    "can_manage": False,
                    "can_send_as": False,
                    "can_send_on_behalf": False,
                },
                format="json",
            )
        self.assertEqual(200, response.status_code)
        (spec, _), _ = ensure.call_args
        self.assertEqual((), spec.authorized_senders)

    def test_member_can_be_removed(self):
        team_box = self.create_team_box()
        created = self.client.post(
            f"/api/team-boxes/{team_box.id}/members/",
            {"mailbox_id": str(self.alice.id), "can_read": True},
            format="json",
        )
        response = self.client.delete(
            f"/api/team-boxes/{team_box.id}/members/{created.data['id']}/"
        )
        self.assertEqual(204, response.status_code)
        self.assertFalse(
            MailboxAccessGrant.objects.filter(target_mailbox=team_box).exists()
        )

    def test_delete_is_blocked_while_alias_points_to_team_box(self):
        team_box = self.create_team_box()
        Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="help@teambox.test",
            destination_mailbox=team_box,
        )
        response = self.client.delete(f"/api/team-boxes/{team_box.id}/")
        self.assertEqual(409, response.status_code)
        self.assertTrue(Mailbox.objects.filter(pk=team_box.pk).exists())
