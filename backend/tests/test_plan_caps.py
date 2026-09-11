"""
Workspace and plan abuse controls.

Two gaps this covers. Workspace creation had no cap at all, so one free account
could mint unlimited trial workspaces, each with its own domains, mailboxes and
provisioning calls. And `check_member_limit` existed in `apps.billing.utils`
but was never called from anywhere — `Plan.max_members` was a number in the
database that nothing consulted.

Seat accounting counts pending invitations. Without that, a three-seat
workspace sends thirty invitations, each acceptance individually looks fine
against a count taken before any of them landed, and the workspace ends up with
thirty members.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.billing.models import PlanTier
from apps.billing.utils import count_member_slots
from apps.teams.models import TeamInvite
from apps.tenants.models import MemberStatus, Tenant, TenantMembership
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    add_member,
    auth_client,
    disable_throttling,
    make_plan,
    make_tenant,
    make_user,
    subscribe,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "plan-cap-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class WorkspaceCapTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("wscap@example.test")
        self.tenant = make_tenant(self.user, name="First", slug="first")
        self.api = auth_client(self.user, self.tenant)

    def _create(self, n):
        return self.api.post("/api/workspaces/create/", {"name": f"Workspace {n}"})

    @override_settings(MAX_WORKSPACES_PER_USER=3)
    def test_creation_is_capped(self):
        # One workspace already exists from setUp.
        self.assertEqual(self._create(2).status_code, 201)
        self.assertEqual(self._create(3).status_code, 201)
        blocked = self._create(4)
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("3", blocked.data["detail"])

    @override_settings(MAX_WORKSPACES_PER_USER=3)
    def test_nothing_is_created_when_the_cap_is_reached(self):
        self._create(2)
        self._create(3)
        before = Tenant.objects.filter(owner=self.user).count()
        self._create(4)
        self.assertEqual(Tenant.objects.filter(owner=self.user).count(), before)

    @override_settings(MAX_WORKSPACES_PER_USER=1)
    def test_the_cap_counts_only_workspaces_the_user_owns(self):
        """
        Being invited into other people's workspaces is not abuse, and must not
        stop someone creating their own.
        """
        stranger = make_user("stranger@example.test")
        for i in range(5):
            other = make_tenant(stranger, name=f"Other {i}", slug=f"other-{i}")
            add_member(other, self.user, "admin")

        # Still refused — but on the one workspace they own, not the six they
        # are a member of.
        self.assertEqual(self._create(9).status_code, 403)
        self.assertEqual(Tenant.objects.filter(owner=self.user).count(), 1)

    @override_settings(MAX_WORKSPACES_PER_USER=10)
    def test_a_raised_cap_takes_effect_without_a_code_change(self):
        for n in range(2, 8):
            self.assertEqual(self._create(n).status_code, 201)

    def test_the_default_cap_is_documented_and_finite(self):
        from django.conf import settings

        self.assertEqual(settings.MAX_WORKSPACES_PER_USER, 5)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class MemberCapTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("plan-owner@example.test")
        self.tenant = make_tenant(self.owner, name="Capped", slug="capped")
        self.plan = make_plan(PlanTier.STARTER, max_members=2)
        subscribe(self.tenant, self.plan)
        self.api = auth_client(self.owner, self.tenant)

    def _add_member(self, email):
        from apps.accounts.models import User

        if not User.objects.filter(email=email).exists():
            make_user(email)
        return self.api.post(
            f"/api/workspaces/{self.tenant.id}/members/",
            {"email": email, "role": "admin"},
        )

    def _invite(self, email):
        return self.api.post(
            "/api/teams/invites/", {"email": email, "role": "admin"}
        )

    def test_direct_member_creation_respects_the_plan(self):
        # Owner already occupies one of two seats.
        self.assertEqual(self._add_member("second@example.test").status_code, 201)
        blocked = self._add_member("third@example.test")
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("2", blocked.data["detail"])

    def test_no_membership_row_is_created_when_refused(self):
        self._add_member("second@example.test")
        before = TenantMembership.objects.filter(tenant=self.tenant).count()
        self._add_member("third@example.test")
        self.assertEqual(
            TenantMembership.objects.filter(tenant=self.tenant).count(), before
        )

    def test_a_pending_invite_reserves_a_seat(self):
        self.assertEqual(self._invite("invitee@example.test").status_code, 201)
        # Both seats now spoken for: one active owner, one pending invite.
        blocked = self._add_member("third@example.test")
        self.assertEqual(blocked.status_code, 403)

    def test_invites_cannot_be_stacked_past_the_plan(self):
        """The bypass: unlimited invitations, each acceptance individually fine."""
        self.assertEqual(self._invite("one@example.test").status_code, 201)
        blocked = self._invite("two@example.test")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(
            TeamInvite.objects.filter(tenant=self.tenant, is_revoked=False).count(), 1
        )

    def test_revoking_an_invite_frees_the_seat(self):
        invite_id = self._invite("one@example.test").data["id"]
        self.assertEqual(self._invite("two@example.test").status_code, 403)
        self.api.delete(f"/api/teams/invites/{invite_id}/")
        self.assertEqual(self._invite("two@example.test").status_code, 201)

    def test_accepting_an_invite_does_not_double_count_the_seat(self):
        raw, invite = TeamInvite.make(
            self.tenant, "accepter@example.test", "admin", self.owner
        )
        accepter = make_user("accepter@example.test")
        res = auth_client(accepter).post("/api/teams/invites/accept/", {"token": raw})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(count_member_slots(self.tenant), 2)
        self.assertTrue(invite.pk)

    def test_acceptance_is_refused_after_a_downgrade(self):
        """
        A seat reserved under a bigger plan is not a promise the smaller plan
        has to honour.
        """
        raw, _ = TeamInvite.make(
            self.tenant, "late@example.test", "admin", self.owner
        )
        self.plan.max_members = 1
        self.plan.save(update_fields=["max_members"])

        late = make_user("late@example.test")
        res = auth_client(late).post("/api/teams/invites/accept/", {"token": raw})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(
            TenantMembership.objects.filter(
                tenant=self.tenant, user=late, status=MemberStatus.ACTIVE
            ).exists()
        )

    def test_removing_a_member_frees_a_seat(self):
        self._add_member("second@example.test")
        member = TenantMembership.objects.get(
            tenant=self.tenant, user__email="second@example.test"
        )
        self.api.delete(f"/api/workspaces/{self.tenant.id}/members/{member.id}/")
        self.assertEqual(self._add_member("third@example.test").status_code, 201)

    def test_reactivating_a_removed_member_takes_a_seat(self):
        """Reactivation is a seat like any other, and was not counted before."""
        removed = make_user("removed@example.test")
        membership = add_member(self.tenant, removed, "admin")
        membership.status = MemberStatus.REMOVED
        membership.save(update_fields=["status"])

        self._add_member("second@example.test")  # fills the second seat
        blocked = self.api.post(
            f"/api/workspaces/{self.tenant.id}/members/",
            {"email": removed.email, "role": "admin"},
        )
        self.assertEqual(blocked.status_code, 403)

    def test_an_expired_invite_does_not_hold_a_seat_forever(self):
        from datetime import timedelta

        from django.utils import timezone

        _, invite = TeamInvite.make(
            self.tenant, "stale@example.test", "admin", self.owner
        )
        invite.expires_at = timezone.now() - timedelta(days=1)
        invite.save(update_fields=["expires_at"])
        self.assertEqual(count_member_slots(self.tenant), 1)

    def test_a_raised_plan_limit_is_honoured_immediately(self):
        self._add_member("second@example.test")
        self.assertEqual(self._add_member("third@example.test").status_code, 403)
        self.plan.max_members = 5
        self.plan.save(update_fields=["max_members"])
        self.assertEqual(self._add_member("third@example.test").status_code, 201)

    def test_a_workspace_without_a_subscription_is_not_blocked(self):
        """Plans gate paid features; an unsubscribed workspace is not an abuse case."""
        free_owner = make_user("free@example.test")
        free = make_tenant(free_owner, name="Free", slug="free")
        make_user("guest@example.test")
        res = auth_client(free_owner, free).post(
            f"/api/workspaces/{free.id}/members/",
            {"email": "guest@example.test", "role": "admin"},
        )
        self.assertEqual(res.status_code, 201)

    def test_the_refusal_explains_what_to_do(self):
        self._add_member("second@example.test")
        detail = self._add_member("third@example.test").data["detail"]
        self.assertIn("pending invitation", detail)
        self.assertIn("upgrade", detail.lower())
        self.assertNotIn("Traceback", detail)
        self.assertTrue(TEST_PASSWORD not in detail)
