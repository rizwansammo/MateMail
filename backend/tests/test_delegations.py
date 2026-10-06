from unittest import mock

from django.test import TestCase, override_settings

from apps.delegations.services import sync_delegated_mailbox
from apps.mail_directory.models import AccessGrantKind, MailboxAccessGrant
from apps.mail_engine.dto import MailboxSpec
from apps.mailboxes.models import Mailbox, MailboxKind, MailboxStatus
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_user,
)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class DelegationApiTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@delegation.test")
        self.tenant = make_tenant(
            self.owner,
            name="Delegation Co",
            slug="delegation-co",
        )
        self.domain = make_domain(self.tenant, "delegation.test")
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
        self.client = auth_client(self.owner, self.tenant)

    def create(self, **overrides):
        payload = {
            "target_mailbox_id": str(self.target.id),
            "delegate_mailbox_id": str(self.delegate.id),
            "can_read": True,
            "can_manage": False,
            "can_send_as": False,
            "can_send_on_behalf": False,
            **overrides,
        }
        return self.client.post("/api/delegations/", payload, format="json")

    def test_read_only_delegation_does_not_touch_sender_authorization(self):
        with mock.patch(
            "apps.delegations.services.sync_delegated_mailbox"
        ) as sync:
            response = self.create()

        self.assertEqual(201, response.status_code, response.data)
        grant = MailboxAccessGrant.objects.get(
            target_mailbox=self.target,
            grantee_mailbox=self.delegate,
        )
        self.assertEqual(AccessGrantKind.DELEGATION, grant.grant_type)
        self.assertTrue(grant.can_read)
        sync.assert_not_called()

    def test_send_as_delegation_reconciles_personal_mailbox_sender_authorization(self):
        with mock.patch(
            "apps.delegations.services.get_adapter"
        ) as get_adapter:
            response = self.create(can_send_as=True)

        self.assertEqual(201, response.status_code, response.data)
        adapter = get_adapter.return_value
        adapter.ensure_mailbox.assert_called_once()
        spec, password = adapter.ensure_mailbox.call_args.args
        self.assertIsInstance(spec, MailboxSpec)
        self.assertTrue(spec.login_enabled)
        self.assertEqual((self.delegate.email,), spec.authorized_senders)
        self.assertEqual("", password)

    def test_send_on_behalf_uses_same_engine_sender_authorization(self):
        with mock.patch(
            "apps.delegations.services.get_adapter"
        ) as get_adapter:
            response = self.create(
                can_read=False,
                can_send_on_behalf=True,
            )

        self.assertEqual(201, response.status_code, response.data)
        spec, _ = get_adapter.return_value.ensure_mailbox.call_args.args
        self.assertEqual((self.delegate.email,), spec.authorized_senders)

    def test_self_delegation_is_rejected(self):
        response = self.create(
            delegate_mailbox_id=str(self.target.id),
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("delegate_mailbox_id", response.data)

    def test_teambox_cannot_be_target_or_delegate(self):
        team_box = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="support",
            full_name="Support",
            kind=MailboxKind.TEAM_BOX,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )

        target_response = self.create(target_mailbox_id=str(team_box.id))
        delegate_response = self.create(delegate_mailbox_id=str(team_box.id))

        self.assertEqual(400, target_response.status_code)
        self.assertEqual(400, delegate_response.status_code)

    def test_cross_tenant_delegate_is_rejected(self):
        other_owner = make_user("owner@other-delegation.test")
        other_tenant = make_tenant(
            other_owner,
            name="Other Delegation",
            slug="other-delegation",
        )
        other_domain = make_domain(other_tenant, "other-delegation.test")
        outsider = Mailbox.objects.create(
            tenant=other_tenant,
            domain=other_domain,
            local_part="outsider",
            full_name="Outsider",
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )

        response = self.create(delegate_mailbox_id=str(outsider.id))
        self.assertEqual(400, response.status_code)
        self.assertIn("delegate_mailbox_id", response.data)

    def test_manage_requires_read(self):
        response = self.create(
            can_read=False,
            can_manage=True,
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("can_manage", response.data)

    def test_duplicate_pair_is_rejected(self):
        self.assertEqual(201, self.create().status_code)
        second = self.create()
        self.assertEqual(400, second.status_code)
        self.assertIn("delegate_mailbox_id", second.data)

    def test_adding_sender_right_updates_engine_authorization(self):
        created = self.create()
        self.assertEqual(201, created.status_code)

        with mock.patch(
            "apps.delegations.services.get_adapter"
        ) as get_adapter:
            response = self.client.patch(
                f"/api/delegations/{created.data['id']}/",
                {"can_send_as": True},
                format="json",
            )

        self.assertEqual(200, response.status_code, response.data)
        spec, _ = get_adapter.return_value.ensure_mailbox.call_args.args
        self.assertEqual((self.delegate.email,), spec.authorized_senders)

    def test_removing_last_sender_right_removes_engine_authorization(self):
        with mock.patch("apps.delegations.services.get_adapter"):
            created = self.create(can_send_as=True)
        self.assertEqual(201, created.status_code)

        with mock.patch(
            "apps.delegations.services.get_adapter"
        ) as get_adapter:
            response = self.client.patch(
                f"/api/delegations/{created.data['id']}/",
                {"can_send_as": False},
                format="json",
            )

        self.assertEqual(200, response.status_code, response.data)
        spec, _ = get_adapter.return_value.ensure_mailbox.call_args.args
        self.assertEqual((), spec.authorized_senders)

    def test_delete_sender_delegation_removes_engine_authorization(self):
        with mock.patch("apps.delegations.services.get_adapter"):
            created = self.create(can_send_as=True)
        self.assertEqual(201, created.status_code)

        with mock.patch(
            "apps.delegations.services.get_adapter"
        ) as get_adapter:
            response = self.client.delete(
                f"/api/delegations/{created.data['id']}/"
            )

        self.assertEqual(204, response.status_code)
        spec, _ = get_adapter.return_value.ensure_mailbox.call_args.args
        self.assertEqual((), spec.authorized_senders)

    def test_mailbox_spec_ignores_inactive_delegate_for_sender_right(self):
        grant = MailboxAccessGrant.objects.create(
            tenant=self.tenant,
            target_mailbox=self.target,
            grantee_mailbox=self.delegate,
            grant_type=AccessGrantKind.DELEGATION,
            can_read=False,
            can_send_as=True,
            active=True,
        )
        self.delegate.status = MailboxStatus.DISABLED
        self.delegate.save(update_fields=["status"])

        spec = MailboxSpec.from_model(self.target)

        self.assertEqual((), spec.authorized_senders)
        self.assertTrue(grant.active)

    def test_service_preserves_personal_login_capability(self):
        MailboxAccessGrant.objects.create(
            tenant=self.tenant,
            target_mailbox=self.target,
            grantee_mailbox=self.delegate,
            grant_type=AccessGrantKind.DELEGATION,
            can_read=False,
            can_send_as=True,
            active=True,
        )
        with mock.patch(
            "apps.delegations.services.get_adapter"
        ) as get_adapter:
            sync_delegated_mailbox(self.target)

        spec, password = get_adapter.return_value.ensure_mailbox.call_args.args
        self.assertTrue(spec.login_enabled)
        self.assertEqual("", password)
