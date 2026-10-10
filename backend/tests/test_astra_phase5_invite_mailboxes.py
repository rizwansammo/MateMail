"""Phase 5 optional mailbox-on-invite: isolated policy and atomicity tests."""
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.domains.models import DomainOwnership
from apps.mailboxes.models import Mailbox
from apps.teams.models import TeamInvite
from apps.tenants.models import MemberStatus, TenantMembership
from tests.factories import (
    FAST_PASSWORD_HASHERS, TEST_PASSWORD, auth_client,
    disable_throttling, make_domain, make_mailbox,
    make_tenant, make_user,
)

MAIL_PASSWORD = "Separate-Mail-Pass-937"


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class InviteMailboxPhase5Tests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@acme.test")
        self.tenant = make_tenant(self.owner, name="Acme", slug="phase5-acme")
        self.domain = make_domain(self.tenant, "acme.test")
        self.admin = auth_client(self.owner, self.tenant)

    def invite(self, email="colleague@acme.test", *, create_mailbox=True):
        with mock.patch("apps.teams.views.send_transactional", return_value=True):
            return self.admin.post(
                "/api/teams/invites/",
                {"email": email, "role": "support", "create_mailbox": create_mailbox},
                format="json",
            )

    def test_legacy_default_remains_hub_only(self):
        with mock.patch("apps.teams.views.send_transactional", return_value=True):
            res = self.admin.post(
                "/api/teams/invites/",
                {"email": "hubonly@acme.test", "role": "read_only"},
                format="json",
            )
        self.assertEqual(res.status_code, 201)
        self.assertFalse(res.data["create_mailbox"])
        self.assertFalse(Mailbox.objects.filter(email="hubonly@acme.test").exists())

    def test_opt_in_creates_only_intent_and_one_time_link(self):
        res = self.invite()
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["create_mailbox"])
        self.assertIn("/accept-invite?token=", res.data["invite_url"])
        self.assertFalse(Mailbox.objects.filter(email="colleague@acme.test").exists())
        listing = self.admin.get("/api/teams/invites/")
        self.assertNotIn("invite_url", str(listing.data))

    def test_block_unverified_and_conflicting_mailbox(self):
        self.domain.ownership_status = DomainOwnership.PENDING
        self.domain.save(update_fields=["ownership_status"])
        res = self.invite()
        self.assertEqual(res.status_code, 400)  # membership policy gate
        self.domain.ownership_status = DomainOwnership.VERIFIED
        self.domain.save(update_fields=["ownership_status"])
        make_mailbox(self.tenant, self.domain, "colleague")
        res = self.invite()
        self.assertEqual(res.status_code, 409)
        self.assertEqual(TeamInvite.objects.filter(email="colleague@acme.test").count(), 0)

    def test_mailbox_quota_gate_applies_only_if_requested(self):
        with mock.patch(
            "apps.teams.invite_mailbox.check_mailbox_limit",
            return_value=(False, "Mailbox capacity reached."),
        ):
            res = self.invite()
        self.assertEqual(res.status_code, 402)
        self.assertIn("capacity", res.data["detail"])

    def test_existing_member_accept_creates_personal_mailbox(self):
        res = self.invite()
        person = make_user("colleague@acme.test")
        token = res.data["invite_url"].split("token=", 1)[1]

        def provision(mailbox, password):
            self.assertEqual(password, MAIL_PASSWORD)
            mailbox.mail_engine_provisioned = True
            mailbox.save(update_fields=["mail_engine_provisioned"])

        with mock.patch("apps.mailboxes.views._provision_mailbox", side_effect=provision):
            accepted = auth_client(person).post(
                "/api/teams/invites/accept/",
                {"token": token, "mailbox_password": MAIL_PASSWORD},
                format="json",
            )
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.data["mailbox"]["mail_service_ready"])
        self.assertEqual(accepted.data["mailbox"]["email"], "colleague@acme.test")
        self.assertEqual(Mailbox.objects.filter(email="colleague@acme.test").count(), 1)
        self.assertTrue(TenantMembership.objects.filter(
            tenant=self.tenant, user=person, status=MemberStatus.ACTIVE,
        ).exists())
        # One-time link cannot provision again.
        again = auth_client(person).post(
            "/api/teams/invites/accept/",
            {"token": token, "mailbox_password": MAIL_PASSWORD},
            format="json",
        )
        self.assertEqual(again.status_code, 400)
        self.assertEqual(Mailbox.objects.filter(email="colleague@acme.test").count(), 1)

    def test_missing_password_does_not_accept_or_take_mailbox(self):
        res = self.invite()
        person = make_user("colleague@acme.test")
        token = res.data["invite_url"].split("token=", 1)[1]
        result = auth_client(person).post(
            "/api/teams/invites/accept/", {"token": token}, format="json"
        )
        self.assertEqual(result.status_code, 400)
        self.assertFalse(Mailbox.objects.filter(email=person.email).exists())
        self.assertFalse(TenantMembership.objects.filter(
            tenant=self.tenant, user=person, status=MemberStatus.ACTIVE,
        ).exists())
        self.assertTrue(TeamInvite.objects.get(email=person.email).is_pending)

    def test_address_conflict_after_invite_rolls_back_acceptance(self):
        res = self.invite()
        person = make_user("colleague@acme.test")
        make_mailbox(self.tenant, self.domain, "colleague")
        token = res.data["invite_url"].split("token=", 1)[1]
        accepted = auth_client(person).post(
            "/api/teams/invites/accept/",
            {"token": token, "mailbox_password": MAIL_PASSWORD},
            format="json",
        )
        self.assertEqual(accepted.status_code, 409)
        self.assertFalse(TenantMembership.objects.filter(
            tenant=self.tenant, user=person, status=MemberStatus.ACTIVE,
        ).exists())
        self.assertTrue(TeamInvite.objects.get(email=person.email).is_pending)

    def test_new_user_invite_signup_creates_one_user_one_mailbox(self):
        res = self.invite(email="newhire@acme.test")
        token = res.data["invite_url"].split("token=", 1)[1]
        with mock.patch("apps.mailboxes.views._provision_mailbox"), \
             mock.patch("apps.accounts.views._send_verification_email"):
            created = APIClient().post(
                "/api/auth/signup/",
                {
                    "email": "newhire@acme.test",
                    "password": TEST_PASSWORD,
                    "full_name": "New Hire",
                    "invite_token": token,
                    "mailbox_password": MAIL_PASSWORD,
                },
                format="json",
            )
        self.assertEqual(created.status_code, 201)
        person = User.objects.get(email="newhire@acme.test")
        self.assertTrue(TenantMembership.objects.filter(
            tenant=self.tenant, user=person, status=MemberStatus.ACTIVE
        ).exists())
        self.assertEqual(Mailbox.objects.filter(email=person.email).count(), 1)
        self.assertFalse(created.data["mailbox"]["mail_service_ready"])

    def test_new_user_wrong_or_missing_password_does_not_create_user(self):
        res = self.invite(email="newhire@acme.test")
        token = res.data["invite_url"].split("token=", 1)[1]
        result = APIClient().post(
            "/api/auth/signup/",
            {
                "email": "newhire@acme.test",
                "password": TEST_PASSWORD,
                "full_name": "New Hire",
                "invite_token": token,
            },
            format="json",
        )
        self.assertEqual(result.status_code, 400)
        self.assertFalse(User.objects.filter(email="newhire@acme.test").exists())
        self.assertTrue(TeamInvite.objects.get(email="newhire@acme.test").is_pending)

    def test_revoked_invitation_never_creates_mailbox(self):
        res = self.invite()
        inv_id = res.data["id"]
        self.assertEqual(
            self.admin.delete(f"/api/teams/invites/{inv_id}/").status_code,
            204,
        )
        self.assertFalse(Mailbox.objects.filter(email="colleague@acme.test").exists())

    def test_unverified_admin_cannot_request_mailbox_but_may_invite_to_hub(self):
        self.owner.email_verified = False
        self.owner.save(update_fields=["email_verified"])
        blocked = self.invite(email="unverified-admin@acme.test")
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("Verify", blocked.data["detail"])
        self.assertFalse(TeamInvite.objects.filter(email="unverified-admin@acme.test").exists())
        hub_invite = self.invite(email="hub-only@acme.test", create_mailbox=False)
        self.assertEqual(hub_invite.status_code, 201)
        self.assertFalse(hub_invite.data["create_mailbox"])

    def test_inviter_verification_rechecked_before_mailbox_acceptance(self):
        res = self.invite()
        user = make_user("colleague@acme.test")
        self.owner.email_verified = False
        self.owner.save(update_fields=["email_verified"])
        token = res.data["invite_url"].split("token=", 1)[1]
        with mock.patch("apps.mailboxes.views._provision_mailbox") as engine:
            accepted = auth_client(user).post(
                "/api/teams/invites/accept/",
                {"token": token, "mailbox_password": MAIL_PASSWORD},
                format="json",
            )
        self.assertEqual(accepted.status_code, 403)
        self.assertTrue(TeamInvite.objects.get(email=user.email).is_pending)
        self.assertFalse(Mailbox.objects.filter(email=user.email).exists())
        self.assertFalse(TenantMembership.objects.filter(
            tenant=self.tenant, user=user, status=MemberStatus.ACTIVE,
        ).exists())
        engine.assert_not_called()

    def test_unverified_new_invitee_can_receive_verification_into_new_mailbox(self):
        """Mailbox address must exist before its verification email can arrive."""
        res = self.invite(email="newjoiner@acme.test")
        token = res.data["invite_url"].split("token=", 1)[1]
        def create_in_engine(mailbox, password):
            self.assertEqual(password, MAIL_PASSWORD)
            mailbox.mail_engine_provisioned = True
            mailbox.save(update_fields=["mail_engine_provisioned"])
        with (
            mock.patch("apps.mailboxes.views._provision_mailbox", side_effect=create_in_engine) as engine,
            mock.patch("apps.accounts.views._send_verification_email") as verification,
        ):
            created = APIClient().post("/api/auth/signup/", {
                "email": "newjoiner@acme.test",
                "password": TEST_PASSWORD,
                "full_name": "New Joiner",
                "invite_token": token,
                "mailbox_password": MAIL_PASSWORD,
            }, format="json")
        self.assertEqual(created.status_code, 201)
        user = User.objects.get(email="newjoiner@acme.test")
        self.assertFalse(user.email_verified)
        self.assertTrue(created.data["mailbox"]["mail_service_ready"])
        engine.assert_called_once()
        verification.assert_called_once_with(user)

    def test_unverified_inviter_cannot_bypass_gate_via_signup(self):
        res = self.invite(email="newjoiner@acme.test")
        token = res.data["invite_url"].split("token=", 1)[1]
        self.owner.email_verified = False
        self.owner.save(update_fields=["email_verified"])
        with mock.patch("apps.mailboxes.views._provision_mailbox") as engine:
            created = APIClient().post("/api/auth/signup/", {
                "email": "newjoiner@acme.test",
                "password": TEST_PASSWORD,
                "full_name": "New Joiner",
                "invite_token": token,
                "mailbox_password": MAIL_PASSWORD,
            }, format="json")
        self.assertEqual(created.status_code, 403)
        self.assertFalse(User.objects.filter(email="newjoiner@acme.test").exists())
        self.assertFalse(Mailbox.objects.filter(email="newjoiner@acme.test").exists())
        self.assertTrue(TeamInvite.objects.get(email="newjoiner@acme.test").is_pending)
        engine.assert_not_called()

    def test_mail_engine_failure_preserves_pending_mailbox_and_successful_membership(self):
        from apps.mail_engine.errors import EngineUnavailable

        res = self.invite(email="retry@acme.test")
        person = make_user("retry@acme.test")
        token = res.data["invite_url"].split("token=", 1)[1]
        with mock.patch(
            "apps.mailboxes.views._provision_mailbox",
            side_effect=EngineUnavailable("synthetic engine outage"),
        ):
            accepted = auth_client(person).post(
                "/api/teams/invites/accept/",
                {"token": token, "mailbox_password": MAIL_PASSWORD},
                format="json",
            )
        self.assertEqual(accepted.status_code, 200)
        self.assertFalse(accepted.data["mailbox"]["mail_service_ready"])
        self.assertTrue(TenantMembership.objects.filter(
            tenant=self.tenant, user=person, status=MemberStatus.ACTIVE
        ).exists())
        mailbox = Mailbox.objects.get(email=person.email)
        self.assertFalse(mailbox.mail_engine_provisioned)
        self.assertFalse(TeamInvite.objects.get(email=person.email).is_pending)

    def test_mailbox_secret_never_persisted_on_invite_or_in_api_responses(self):
        result = self.invite(email="no-secret@acme.test")
        self.assertEqual(result.status_code, 201)
        invite = TeamInvite.objects.get(email="no-secret@acme.test")
        self.assertNotIn(MAIL_PASSWORD, str(invite.__dict__))
        listing = self.admin.get("/api/teams/invites/")
        self.assertNotIn("mailbox_password", str(listing.data))
        preview = APIClient().get(
            "/api/teams/invites/preview/",
            {"token": result.data["invite_url"].split("token=", 1)[1]},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertNotIn("invite_url", str(preview.data))
        self.assertNotIn("mailbox_password", str(preview.data))
