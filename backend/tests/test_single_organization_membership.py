from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import DomainOwnership
from apps.teams.models import TeamInvite
from apps.tenants.models import MemberStatus, Tenant, TenantMembership
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    add_member,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_unverified_domain,
    make_user,
)


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class OrganizationMembershipPolicyTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@acme.test")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        make_domain(self.tenant, "acme.test")
        self.api = auth_client(self.owner, self.tenant)

    def test_team_invite_allows_verified_organization_domain(self):
        response = self.api.post(
            "/api/teams/invites/",
            {"email": "person@acme.test", "role": "admin"},
        )
        self.assertEqual(response.status_code, 201)

    def test_team_invite_rejects_external_domain(self):
        response = self.api.post(
            "/api/teams/invites/",
            {"email": "person@outside.test", "role": "admin"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("verified domain", response.data["detail"].lower())
        self.assertFalse(
            TeamInvite.objects.filter(tenant=self.tenant, email="person@outside.test").exists()
        )

    def test_unverified_domain_does_not_authorize_members(self):
        make_unverified_domain(self.tenant, "pending.test")
        response = self.api.post(
            "/api/teams/invites/",
            {"email": "person@pending.test", "role": "support"},
        )
        self.assertEqual(response.status_code, 400)

    def test_legacy_direct_member_add_obeys_the_same_domain_rule(self):
        outsider = make_user("outsider@outside.test")
        response = self.api.post(
            f"/api/workspaces/{self.tenant.id}/members/",
            {"email": outsider.email, "role": "admin"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            TenantMembership.objects.filter(
                tenant=self.tenant,
                user=outsider,
                status=MemberStatus.ACTIVE,
            ).exists()
        )

    def test_existing_account_cannot_become_active_in_second_organization(self):
        person = make_user("person@acme.test")
        other_owner = make_user("other@other.test")
        other = make_tenant(other_owner, name="Other", slug="other")
        add_member(other, person, "admin")

        response = self.api.post(
            "/api/teams/invites/",
            {"email": person.email, "role": "admin"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("already assigned", response.data["detail"].lower())

    def test_invite_accept_rechecks_domain_ownership(self):
        person = make_user("person@acme.test")
        raw, invite = TeamInvite.make(self.tenant, person.email, "admin", self.owner)

        domain = self.tenant.domains.get(domain="acme.test")
        domain.ownership_status = DomainOwnership.PENDING
        domain.ownership_verified_at = None
        domain.save(update_fields=["ownership_status", "ownership_verified_at"])

        response = auth_client(person).post(
            "/api/teams/invites/accept/",
            {"token": raw},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(
            TenantMembership.objects.filter(
                tenant=self.tenant, user=person, status=MemberStatus.ACTIVE
            ).first()
        )
        invite.refresh_from_db()
        self.assertIsNone(invite.accepted_at)


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class InviteBoundSignupTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@acme.test")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        make_domain(self.tenant, "acme.test")

    def test_invite_signup_creates_user_inside_existing_organization_only(self):
        raw, invite = TeamInvite.make(
            self.tenant,
            "newperson@acme.test",
            "support",
            self.owner,
        )
        tenant_count = Tenant.objects.count()

        response = APIClient().post(
            "/api/auth/signup/",
            {
                "email": "newperson@acme.test",
                "password": TEST_PASSWORD,
                "full_name": "New Person",
                "invite_token": raw,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Tenant.objects.count(), tenant_count)
        self.assertEqual(response.data["tenant"]["id"], str(self.tenant.id))
        membership = TenantMembership.objects.get(
            tenant=self.tenant,
            user__email="newperson@acme.test",
        )
        self.assertEqual(membership.role, "support")
        self.assertEqual(membership.status, MemberStatus.ACTIVE)
        invite.refresh_from_db()
        self.assertIsNotNone(invite.accepted_at)

    def test_invite_signup_email_is_fixed_by_invitation(self):
        raw, _ = TeamInvite.make(
            self.tenant,
            "newperson@acme.test",
            "admin",
            self.owner,
        )
        response = APIClient().post(
            "/api/auth/signup/",
            {
                "email": "different@acme.test",
                "password": TEST_PASSWORD,
                "full_name": "Different",
                "invite_token": raw,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("sent to", response.data["email"].lower())

    def test_normal_signup_still_creates_exactly_one_new_organization(self):
        before = Tenant.objects.count()
        response = APIClient().post(
            "/api/auth/signup/",
            {
                "email": "founder@newco.test",
                "password": TEST_PASSWORD,
                "full_name": "Founder",
                "workspace_name": "New Co",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Tenant.objects.count(), before + 1)
        self.assertEqual(
            TenantMembership.objects.filter(
                user__email="founder@newco.test",
                status=MemberStatus.ACTIVE,
            ).count(),
            1,
        )


class RemovedMultiWorkspaceRoutesTest(TestCase):
    def setUp(self):
        self.user = make_user("owner@acme.test")
        self.tenant = make_tenant(self.user, name="Acme", slug="acme")
        self.api = auth_client(self.user, self.tenant)

    def test_additional_workspace_creation_route_is_gone(self):
        self.assertEqual(
            self.api.post("/api/workspaces/create/", {"name": "Second"}).status_code,
            404,
        )

    def test_workspace_switch_route_is_gone(self):
        self.assertEqual(
            self.api.post(
                "/api/workspaces/switch/",
                {"tenant_id": str(self.tenant.id)},
                format="json",
            ).status_code,
            404,
        )
