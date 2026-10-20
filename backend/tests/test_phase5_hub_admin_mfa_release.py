"""Phase 5: release gating and platform refresh security regression tests."""
from io import StringIO
from unittest.mock import patch
import json

from django.core.management import call_command
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.cookies import REFRESH_COOKIE_NAME
from apps.accounts.models import VerifiedRecoveryEmail
from apps.accounts.tokens import make_tokens
from apps.tenants.management.commands.audit_hub_admin_mfa_readiness import evaluate_readiness
from apps.tenants.models import MemberRole, AdminHubMfaMode
from tests.factories import (
    FAST_PASSWORD_HASHERS, make_user, make_tenant, add_member,
    disable_throttling,
)


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "phase5-admin-release",
    }},
)
class Phase5ReleaseTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)

    def test_zero_recovery_addresses_blocks_release_without_disclosing_users(self):
        staff1 = make_user("staff-a@example.test", is_platform_admin=True)
        staff2 = make_user("staff-b@example.test", is_platform_admin=True)
        owner = make_user("owner-release@example.test")
        make_tenant(owner, name="Release Tenant", slug="phase5-release")
        report = evaluate_readiness()
        self.assertFalse(report["ready"])
        self.assertEqual(report["active_platform_operators"], 2)
        self.assertEqual(report["owners_without_independent_recovery"], 1)
        stdout = StringIO()
        with self.assertRaises(SystemExit) as exit_info:
            call_command("audit_hub_admin_mfa_readiness", "--json", stdout=stdout)
        self.assertEqual(exit_info.exception.code, 2)
        data = json.loads(stdout.getvalue())
        self.assertFalse(data["ready"])
        self.assertNotIn(owner.email, stdout.getvalue())
        self.assertNotIn(staff1.email, stdout.getvalue())
        self.assertNotIn(staff2.email, stdout.getvalue())

    def test_all_owners_and_required_admins_with_external_contacts_pass(self):
        make_user("staff-a@example.test", is_platform_admin=True)
        make_user("staff-b@example.test", is_platform_admin=True)
        owner = make_user("owner-release@example.test")
        tenant = make_tenant(owner, name="Release Tenant", slug="phase5-ready")
        admin = make_user("required-admin@example.test")
        membership = add_member(tenant, admin, MemberRole.ADMIN)
        membership.admin_hub_mfa_policy = AdminHubMfaMode.REQUIRED
        membership.save(update_fields=["admin_hub_mfa_policy"])
        for user in (owner, admin):
            VerifiedRecoveryEmail.objects.create(
                user=user, email=user.email.split("@")[0]+"@recovery-outside.test",
                verified_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
            )
        result = evaluate_readiness()
        self.assertTrue(result["ready"], result["blockers"])
        self.assertEqual(result["required_admins_checked"], 1)
        self.assertEqual(result["owners_without_independent_recovery"], 0)

    def test_one_operator_is_not_emergency_recovery_ready(self):
        make_user("staff-one@example.test", is_platform_admin=True)
        owner = make_user("owner-release@example.test")
        make_tenant(owner, name="Tenant", slug="phase5-one-staff")
        VerifiedRecoveryEmail.objects.create(
            user=owner, email="other@outside.test",
            verified_at=__import__("django.utils.timezone", fromlist=["now"]).now(),
        )
        result = evaluate_readiness()
        self.assertFalse(result["ready"])
        self.assertEqual(result["active_platform_operators"], 1)

    def test_platform_verified_refresh_preserves_original_proof_and_epoch(self):
        from django.utils import timezone
        admin = make_user("operator@example.test", is_platform_admin=True)
        client = APIClient()
        tokens = make_tokens(admin, platform_verified=True)
        previous = AccessToken(tokens["access"])
        issued_at = previous["platform_otp_verified_at"]
        client.cookies[REFRESH_COOKIE_NAME] = tokens["refresh"]
        first = client.post("/api/auth/refresh/", format="json")
        self.assertEqual(first.status_code, 200, getattr(first, "data", None))
        first_access = AccessToken(first.data["access"])
        self.assertEqual(first_access["platform_otp_verified_at"], issued_at)
        self.assertEqual(first_access["hub_security_epoch"], 0)
        second = client.post("/api/auth/refresh/", format="json")
        self.assertEqual(second.status_code, 200, getattr(second, "data", None))
        second_access = AccessToken(second.data["access"])
        self.assertEqual(second_access["platform_otp_verified_at"], issued_at)
        self.assertEqual(second_access["hub_security_epoch"], 0)

    def test_refresh_does_not_create_platform_verification_for_hub_token(self):
        user = make_user("normal-user@example.test")
        tenant = make_tenant(user, name="Tenant", slug="phase5-no-proof")
        tokens = make_tokens(user, tenant_id=tenant.pk)
        client = APIClient()
        client.cookies[REFRESH_COOKIE_NAME] = tokens["refresh"]
        response = client.post("/api/auth/refresh/", format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertNotIn("platform_otp_verified_at", AccessToken(response.data["access"]))

    def test_refresh_session_revoked_after_epoch_increment(self):
        admin = make_user("epoch-operator@example.test", is_platform_admin=True)
        tokens = make_tokens(admin, platform_verified=True)
        admin.hub_security_epoch += 1
        admin.save(update_fields=["hub_security_epoch"])
        client = APIClient()
        client.cookies[REFRESH_COOKIE_NAME] = tokens["refresh"]
        response = client.post("/api/auth/refresh/", format="json")
        self.assertEqual(response.status_code, 403)
