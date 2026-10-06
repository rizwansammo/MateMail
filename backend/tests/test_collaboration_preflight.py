import io

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.forward_groups.models import ForwardGroup, ForwardGroupMember
from apps.forwarding.models import ForwardingRule, ForwardingStatus
from apps.mail_directory.models import (
    AccessGrantKind,
    AddressClaim,
    MailboxAccessGrant,
)
from apps.mailboxes.models import Mailbox, MailboxKind, MailboxStatus
from tests.factories import make_domain, make_mailbox, make_tenant, make_user


class CollaborationPreflightTest(TestCase):
    def setUp(self):
        owner = make_user("owner@preflight.test")
        self.tenant = make_tenant(owner, name="Preflight", slug="preflight")
        self.domain = make_domain(self.tenant, "preflight.test")
        self.alice = make_mailbox(self.tenant, self.domain, "alice", "Alice")
        self.bob = make_mailbox(self.tenant, self.domain, "bob", "Bob")

    def run_preflight(self):
        out = io.StringIO()
        call_command("collaboration_preflight", stdout=out)
        return out.getvalue()

    def create_group(self):
        group = ForwardGroup.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="engineering",
            display_name="Engineering",
        )
        ForwardGroupMember.objects.create(
            group=group,
            mailbox=self.alice,
        )
        return group

    def test_healthy_collaboration_state_passes(self):
        group = self.create_group()
        MailboxAccessGrant.objects.create(
            tenant=self.tenant,
            target_mailbox=self.bob,
            grantee_mailbox=self.alice,
            grant_type=AccessGrantKind.DELEGATION,
            can_read=True,
        )

        output = self.run_preflight()

        self.assertIn("Collaboration preflight passed", output)
        self.assertIn("Forward Groups", output)
        self.assertTrue(
            AddressClaim.objects.filter(address=group.address).exists()
        )

    def test_missing_address_claim_blocks_preflight(self):
        AddressClaim.objects.filter(address=self.alice.email).delete()

        with self.assertRaisesRegex(
            CommandError,
            "missing AddressClaim",
        ):
            self.run_preflight()

    def test_invalid_access_grant_blocks_preflight(self):
        team_box = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="support",
            full_name="Support",
            kind=MailboxKind.TEAM_BOX,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        grant = MailboxAccessGrant.objects.create(
            tenant=self.tenant,
            target_mailbox=team_box,
            grantee_mailbox=self.alice,
            grant_type=AccessGrantKind.TEAM_BOX,
            can_read=True,
        )
        MailboxAccessGrant.objects.filter(pk=grant.pk).update(
            can_read=False,
            can_manage=True,
        )

        with self.assertRaisesRegex(
            CommandError,
            "invalid mailbox access grant",
        ):
            self.run_preflight()

    def test_active_memberless_forward_group_blocks_preflight(self):
        ForwardGroup.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="empty",
            display_name="Empty Group",
        )

        with self.assertRaisesRegex(
            CommandError,
            "has no members",
        ):
            self.run_preflight()

    def test_forward_group_forwarding_loop_blocks_preflight(self):
        group = self.create_group()
        ForwardingRule.objects.create(
            tenant=self.tenant,
            source_mailbox=self.alice,
            destination_email=group.address,
            status=ForwardingStatus.ACTIVE,
        )

        with self.assertRaisesRegex(
            CommandError,
            "delivery loop",
        ):
            self.run_preflight()
