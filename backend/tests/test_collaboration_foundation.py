from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.aliases.models import Alias
from apps.forwarding.models import ForwardingRule
from apps.mail_directory.models import (
    AccessGrantKind,
    AddressClaim,
    AddressKind,
    MailboxAccessGrant,
)
from apps.mail_directory.services import (
    AddressConflict,
    grant_mailbox_access,
    permissions_for,
)
from apps.mailboxes.models import Mailbox, MailboxKind, MailboxStatus
from apps.postbox.auth import MailboxUnavailable, assert_mailbox_may_sign_in
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
class AddressRegistryTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@directory.test")
        self.tenant = make_tenant(self.owner, name="Directory", slug="directory")
        self.domain = make_domain(self.tenant, "directory.test")
        self.client = auth_client(self.owner, self.tenant)

    def test_mailbox_creation_reserves_the_address(self):
        mailbox = make_mailbox(self.tenant, self.domain, "alice")
        claim = AddressClaim.objects.get(address=mailbox.email)
        self.assertEqual(AddressKind.MAILBOX, claim.kind)
        self.assertEqual(self.tenant.id, claim.tenant_id)

    def test_alias_creation_reserves_the_address(self):
        mailbox = make_mailbox(self.tenant, self.domain, "alice")
        alias = Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="hello@directory.test",
            destination_mailbox=mailbox,
        )
        claim = AddressClaim.objects.get(address=alias.source_address)
        self.assertEqual(AddressKind.ALIAS, claim.kind)

    def test_alias_cannot_claim_an_existing_mailbox_address_case_insensitively(self):
        mailbox = make_mailbox(self.tenant, self.domain, "alice")
        with self.assertRaises(AddressConflict):
            Alias.objects.create(
                tenant=self.tenant,
                domain=self.domain,
                source_address=mailbox.email.upper(),
                destination_mailbox=mailbox,
            )

    def test_mailbox_api_cannot_claim_an_existing_alias_address(self):
        target = make_mailbox(self.tenant, self.domain, "target")
        Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="sales@directory.test",
            destination_mailbox=target,
        )

        response = self.client.post(
            "/api/mailboxes/",
            {
                "local_part": "sales",
                "domain_id": str(self.domain.id),
                "full_name": "Sales",
                "password": "Mailbox-Passphrase-9",
            },
            format="json",
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("local_part", response.data)
        self.assertFalse(
            Mailbox.objects.filter(email__iexact="sales@directory.test").exists()
        )

    def test_alias_api_cannot_claim_an_existing_mailbox_address(self):
        mailbox = make_mailbox(self.tenant, self.domain, "sales")
        response = self.client.post(
            "/api/aliases/",
            {
                "source_local_part": "sales",
                "domain_id": str(self.domain.id),
                "destination_mailbox_id": str(mailbox.id),
            },
            format="json",
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("source_local_part", response.data)

    def test_deleting_a_mailbox_releases_its_claim(self):
        mailbox = make_mailbox(self.tenant, self.domain, "delete-me")
        address = mailbox.email
        mailbox.delete()
        self.assertFalse(AddressClaim.objects.filter(address=address).exists())

    def test_deleting_an_alias_releases_its_claim(self):
        mailbox = make_mailbox(self.tenant, self.domain, "alice")
        alias = Alias.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            source_address="old@directory.test",
            destination_mailbox=mailbox,
        )
        address = alias.source_address
        alias.delete()
        self.assertFalse(AddressClaim.objects.filter(address=address).exists())

    def test_forwarding_rule_does_not_claim_a_recipient_address(self):
        mailbox = make_mailbox(self.tenant, self.domain, "alice")
        ForwardingRule.objects.create(
            tenant=self.tenant,
            source_mailbox=mailbox,
            destination_email="external@example.test",
        )
        self.assertFalse(
            AddressClaim.objects.filter(address="external@example.test").exists()
        )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class MailboxKindTest(TestCase):
    def setUp(self):
        self.owner = make_user("owner@kind.test")
        self.tenant = make_tenant(self.owner, name="Kinds", slug="kinds")
        self.domain = make_domain(self.tenant, "kind.test")

    def test_personal_mailbox_remains_directly_eligible_for_postbox(self):
        mailbox = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="alice",
            full_name="Alice",
            kind=MailboxKind.PERSONAL,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        assert_mailbox_may_sign_in(mailbox)

    def test_teambox_cannot_sign_in_directly(self):
        team_box = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="support",
            full_name="Support",
            kind=MailboxKind.TEAM_BOX,
            status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        with self.assertRaises(MailboxUnavailable):
            assert_mailbox_may_sign_in(team_box)

        self.assertEqual(
            AddressKind.TEAM_BOX,
            AddressClaim.objects.get(address=team_box.email).kind,
        )

    def test_mailbox_kind_is_immutable(self):
        mailbox = make_mailbox(self.tenant, self.domain, "alice")
        mailbox.kind = MailboxKind.TEAM_BOX
        with self.assertRaises(ValidationError):
            mailbox.save()


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class MailboxAccessGrantTest(TestCase):
    def setUp(self):
        self.owner = make_user("owner@grants.test")
        self.tenant = make_tenant(self.owner, name="Grants", slug="grants")
        self.domain = make_domain(self.tenant, "grants.test")
        self.alice = make_mailbox(self.tenant, self.domain, "alice")
        self.bob = make_mailbox(self.tenant, self.domain, "bob")
        self.team_box = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="support",
            full_name="Support",
            kind=MailboxKind.TEAM_BOX,
        )

    def test_same_mailbox_has_intrinsic_full_access(self):
        permissions = permissions_for(
            grantee_mailbox=self.alice,
            target_mailbox=self.alice,
        )
        self.assertTrue(permissions.can_read)
        self.assertTrue(permissions.can_manage)
        self.assertTrue(permissions.can_send_as)
        self.assertTrue(permissions.can_send_on_behalf)

    def test_teambox_membership_permissions_are_resolved(self):
        grant_mailbox_access(
            target_mailbox=self.team_box,
            grantee_mailbox=self.alice,
            grant_type=AccessGrantKind.TEAM_BOX,
            can_read=True,
            can_manage=True,
            can_send_as=True,
        )
        permissions = permissions_for(
            grantee_mailbox=self.alice,
            target_mailbox=self.team_box,
        )
        self.assertTrue(permissions.can_read)
        self.assertTrue(permissions.can_manage)
        self.assertTrue(permissions.can_send_as)
        self.assertFalse(permissions.can_send_on_behalf)

    def test_delegation_targets_a_personal_mailbox(self):
        grant = grant_mailbox_access(
            target_mailbox=self.bob,
            grantee_mailbox=self.alice,
            grant_type=AccessGrantKind.DELEGATION,
            can_read=True,
            can_send_on_behalf=True,
        )
        self.assertEqual(AccessGrantKind.DELEGATION, grant.grant_type)

    def test_cross_tenant_grant_is_rejected(self):
        other_owner = make_user("other@example.test")
        other_tenant = make_tenant(
            other_owner,
            name="Other",
            slug="other-grants",
        )
        other_domain = make_domain(other_tenant, "other-grants.test")
        outsider = make_mailbox(other_tenant, other_domain, "outsider")

        with self.assertRaises(ValidationError):
            grant_mailbox_access(
                target_mailbox=self.team_box,
                grantee_mailbox=outsider,
                grant_type=AccessGrantKind.TEAM_BOX,
            )

    def test_team_box_cannot_be_a_grantee(self):
        with self.assertRaises(ValidationError):
            grant_mailbox_access(
                target_mailbox=self.bob,
                grantee_mailbox=self.team_box,
                grant_type=AccessGrantKind.DELEGATION,
            )

    def test_manage_requires_read(self):
        grant = MailboxAccessGrant(
            tenant=self.tenant,
            target_mailbox=self.team_box,
            grantee_mailbox=self.alice,
            grant_type=AccessGrantKind.TEAM_BOX,
            can_read=False,
            can_manage=True,
        )
        with self.assertRaises(ValidationError):
            grant.save()

    def test_no_grant_means_no_cross_mailbox_access(self):
        permissions = permissions_for(
            grantee_mailbox=self.alice,
            target_mailbox=self.bob,
        )
        self.assertFalse(permissions.can_read)
        self.assertFalse(permissions.can_manage)
        self.assertFalse(permissions.can_send_as)
        self.assertFalse(permissions.can_send_on_behalf)
