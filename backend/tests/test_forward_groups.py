from unittest import mock

from django.test import TestCase, override_settings

from apps.forward_groups.models import (
    ForwardGroup,
    ForwardGroupAllowedSender,
    ForwardGroupMember,
    ForwardGroupSenderPolicy,
)
from apps.forward_groups.services import resolved_allowed_senders
from apps.forwarding.models import ForwardingRule, ForwardingStatus
from apps.mail_directory.models import AddressClaim, AddressKind
from apps.mail_engine.dto import ForwardGroupSpec
from apps.mailboxes.models import Mailbox, MailboxKind
from apps.postbox.sending import allowed_identities
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
class ForwardGroupApiTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@fg.test")
        self.tenant = make_tenant(self.owner, name="FG Co", slug="fg-co")
        self.domain = make_domain(self.tenant, "fg.test")
        self.alice = make_mailbox(self.tenant, self.domain, "alice", "Alice")
        self.bob = make_mailbox(self.tenant, self.domain, "bob", "Bob")
        self.client = auth_client(self.owner, self.tenant)

    def create_group(self, **overrides):
        payload = {
            "local_part": "engineering",
            "domain_id": str(self.domain.id),
            "display_name": "Engineering",
            "member_mailbox_ids": [str(self.alice.id), str(self.bob.id)],
            "sender_policy": "anyone",
            **overrides,
        }
        return self.client.post("/api/forward-groups/", payload, format="json")

    def test_create_group_reserves_address_and_provisions_distribution(self):
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_forward_group"
        ) as ensure:
            response = self.create_group()

        self.assertEqual(201, response.status_code, response.data)
        group = ForwardGroup.objects.get(address="engineering@fg.test")
        self.assertEqual(2, group.members.count())
        claim = AddressClaim.objects.get(address=group.address)
        self.assertEqual(AddressKind.FORWARD_GROUP, claim.kind)

        ensure.assert_called_once()
        (spec,), _ = ensure.call_args
        self.assertIsInstance(spec, ForwardGroupSpec)
        self.assertEqual(
            ("alice@fg.test", "bob@fg.test"),
            spec.destinations,
        )
        self.assertEqual("anyone", spec.sender_policy)
        self.assertEqual((), spec.allowed_senders)

    def test_forward_group_is_not_a_mailbox_or_send_identity(self):
        response = self.create_group()
        self.assertEqual(201, response.status_code)
        group = ForwardGroup.objects.get(address="engineering@fg.test")

        self.assertFalse(Mailbox.objects.filter(email=group.address).exists())
        identities = {item.address for item in allowed_identities(self.alice)}
        self.assertNotIn(group.address, identities)

    def test_address_collision_with_mailbox_is_rejected(self):
        response = self.create_group(local_part="alice")
        self.assertEqual(400, response.status_code)
        self.assertIn("local_part", response.data)

    def test_cross_tenant_member_is_rejected(self):
        other_owner = make_user("other@fg.test")
        other_tenant = make_tenant(other_owner, name="Other", slug="fg-other")
        other_domain = make_domain(other_tenant, "fg-other.test")
        outsider = make_mailbox(other_tenant, other_domain, "outsider")

        response = self.create_group(
            member_mailbox_ids=[str(self.alice.id), str(outsider.id)]
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("member_mailbox_ids", response.data)

    def test_member_ids_are_deduplicated(self):
        response = self.create_group(
            member_mailbox_ids=[
                str(self.alice.id),
                str(self.alice.id),
                str(self.bob.id),
            ]
        )
        self.assertEqual(201, response.status_code)
        group = ForwardGroup.objects.get(address="engineering@fg.test")
        self.assertEqual(2, group.members.count())

    def test_organization_policy_resolves_all_active_personal_mailboxes(self):
        response = self.create_group(sender_policy="organization")
        self.assertEqual(201, response.status_code)
        group = ForwardGroup.objects.get(address="engineering@fg.test")
        self.assertEqual(
            ("alice@fg.test", "bob@fg.test"),
            resolved_allowed_senders(group),
        )

    def test_members_policy_excludes_teambox_members_from_sender_allowlist(self):
        team_box = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="support",
            full_name="Support",
            kind=MailboxKind.TEAM_BOX,
        )
        response = self.create_group(
            member_mailbox_ids=[str(self.alice.id), str(team_box.id)],
            sender_policy="members",
        )
        self.assertEqual(201, response.status_code)
        group = ForwardGroup.objects.get(address="engineering@fg.test")
        self.assertEqual(("alice@fg.test",), resolved_allowed_senders(group))

    def test_selected_policy_uses_only_explicit_personal_senders(self):
        response = self.create_group(
            sender_policy="selected",
            allowed_sender_mailbox_ids=[str(self.bob.id)],
        )
        self.assertEqual(201, response.status_code)
        group = ForwardGroup.objects.get(address="engineering@fg.test")
        self.assertTrue(
            ForwardGroupAllowedSender.objects.filter(
                group=group,
                mailbox=self.bob,
            ).exists()
        )
        self.assertEqual(("bob@fg.test",), resolved_allowed_senders(group))

    def test_member_forwarding_back_to_group_is_rejected(self):
        ForwardingRule.objects.create(
            tenant=self.tenant,
            source_mailbox=self.alice,
            destination_email="engineering@fg.test",
            status=ForwardingStatus.ACTIVE,
        )
        response = self.create_group()
        self.assertEqual(400, response.status_code)
        self.assertIn("member_mailbox_ids", response.data)

    def test_forwarding_rule_to_group_is_rejected_for_group_member(self):
        response = self.create_group()
        self.assertEqual(201, response.status_code)
        group = ForwardGroup.objects.get(address="engineering@fg.test")

        forwarding = self.client.post(
            "/api/forwarding/",
            {
                "source_mailbox_id": str(self.alice.id),
                "destination_email": group.address,
                "keep_copy": True,
            },
            format="json",
        )
        self.assertEqual(400, forwarding.status_code)
        self.assertIn("destination_email", forwarding.data)

    def test_last_member_cannot_be_removed(self):
        response = self.create_group(member_mailbox_ids=[str(self.alice.id)])
        self.assertEqual(201, response.status_code)
        group = ForwardGroup.objects.get(address="engineering@fg.test")
        member = ForwardGroupMember.objects.get(group=group)

        deleted = self.client.delete(
            f"/api/forward-groups/{group.id}/members/{member.id}/"
        )
        self.assertEqual(400, deleted.status_code)
        self.assertTrue(ForwardGroupMember.objects.filter(pk=member.id).exists())

    def test_delete_releases_address_claim(self):
        response = self.create_group()
        self.assertEqual(201, response.status_code)
        group = ForwardGroup.objects.get(address="engineering@fg.test")

        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.delete_forward_group"
        ):
            deleted = self.client.delete(f"/api/forward-groups/{group.id}/")

        self.assertEqual(204, deleted.status_code)
        self.assertFalse(AddressClaim.objects.filter(address=group.address).exists())

    def test_selected_sender_must_be_personal_mailbox(self):
        team_box = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="support",
            full_name="Support",
            kind=MailboxKind.TEAM_BOX,
        )
        response = self.create_group(
            sender_policy="selected",
            allowed_sender_mailbox_ids=[str(team_box.id)],
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("allowed_sender_mailbox_ids", response.data)
