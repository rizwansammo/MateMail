"""Isolated integration tests for owner emergency case and approval."""
from datetime import timedelta
import re
from unittest import mock
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import VerifiedRecoveryEmail, TwoFactorSetup, PasswordResetToken
from apps.accounts.tokens import make_tokens
from apps.platform_admin.models import OwnerEmergencyRecoveryCase
from tests.factories import (
    FAST_PASSWORD_HASHERS, TEST_PASSWORD, bearer_client, disable_throttling,
    enable_2fa, make_tenant, make_user,
)

@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "platform-owner-emergency"}},
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://hub.example.test",
    PLATFORM_OWNER_EMERGENCY_RECOVERY_ENABLED=True,
)
class OwnerEmergencyTest(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        disable_throttling(self)
        self.a = make_user("first-ops@example.test", is_platform_admin=True)
        self.b = make_user("second-ops@example.test", is_platform_admin=True)
        self.owner = make_user("workspace-owner@example.test")
        enable_2fa(self.owner)
        self.tenant = make_tenant(self.owner, name="Recovery", slug="recovery-unique")
        self.external = VerifiedRecoveryEmail.objects.create(
            user=self.owner, email="private-owner@outside.test",
            verified_at=timezone.now(),
        )
        self.client_a = bearer_client(make_tokens(self.a, platform_verified=True)["access"])
        self.client_b = bearer_client(make_tokens(self.b, platform_verified=True)["access"])
        self.url = f"/api/platform/tenants/{self.tenant.pk}/owner/emergency/"

    def request(self, mode="mfa", client=None):
        return (client or self.client_a).post(self.url, {
            "mode": mode,
            "reason": "Owner has permanently lost authenticator access",
            "evidence_reference": "CASE-RECOVERY-1001",
            "verification_note": "Independent evidence confirming owner identity was reviewed",
            "current_password": TEST_PASSWORD,
        }, format="json")

    def case(self):
        return OwnerEmergencyRecoveryCase.objects.get(tenant=self.tenant)

    def action(self, action, client=None):
        return (client or self.client_b).post(
            f"{self.url}{self.case().pk}/{action}/", {
                "current_password":TEST_PASSWORD,
                **({"verification_note": "A second independent identity review was completed"} if action=="approve" else {}),
            }, format="json",
        )

    def prepare(self, mode="mfa"):
        self.assertEqual(self.request(mode).status_code, 201)
        self.assertEqual(self.action("approve").status_code, 200)
        self.case().refresh_from_db()
        OwnerEmergencyRecoveryCase.objects.update(
            hold_until=timezone.now()-timedelta(seconds=1)
        )
        self.assertEqual(self.action("dispatch").status_code, 200)
        message=next(m for m in mail.outbox if "Confirm your workspace owner" in m.subject)
        self.assertEqual(message.to, [self.external.email])
        return re.search(r"capability=([A-Za-z0-9_-]+)", message.body).group(1)

    def confirm(self, value):
        return APIClient().post("/api/auth/owner-emergency/confirm/", {
            "capability":value,"confirm":True,
        }, format="json")

    def test_disabled_feature(self):
        with override_settings(PLATFORM_OWNER_EMERGENCY_RECOVERY_ENABLED=False):
            self.assertEqual(self.request().status_code, 503)

    def test_owner_external_contact_is_required(self):
        self.external.verified_at = None
        self.external.save(update_fields=["verified_at"])
        self.assertEqual(self.request().status_code, 409)

    def test_operator_cannot_self_approve(self):
        self.assertEqual(self.request().status_code, 201)
        self.assertEqual(self.action("approve",self.client_a).status_code, 403)

    def test_hold_blocks_dispatch(self):
        self.assertEqual(self.request().status_code, 201)
        self.assertEqual(self.action("approve").status_code, 200)
        self.assertEqual(self.action("dispatch").status_code, 409)
        self.assertTrue(TwoFactorSetup.objects.filter(user=self.owner).exists())

    def test_mfa_recovery_is_one_time_and_requires_enrollment(self):
        old = make_tokens(self.owner, tenant_id=self.tenant.pk, mfa_verified=True)
        token = self.prepare()
        self.assertEqual(self.confirm(token).status_code, 200)
        self.assertEqual(self.confirm(token).status_code, 401)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.two_factor_enabled)
        self.assertTrue(self.owner.hub_mfa_reenrollment_required)
        self.assertEqual(self.owner.hub_security_epoch, 1)
        self.assertIn(bearer_client(old["access"]).get("/api/auth/me/").status_code,(401,403))

    def test_full_recovery_requires_new_password(self):
        token = self.prepare("full")
        self.assertEqual(self.confirm(token).status_code, 200)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.has_usable_password())
        self.assertTrue(self.owner.hub_mfa_reenrollment_required)
        self.assertTrue(PasswordResetToken.objects.filter(user=self.owner,is_used=False).exists())
        notification=next(m for m in mail.outbox if "Complete your owner password" in m.subject)
        self.assertEqual(notification.to, [self.external.email])

    def test_delivery_failure_leaves_credentials_intact(self):
        token = self.prepare("full")
        with mock.patch("apps.platform_admin.owner_emergency_views.send_transactional", return_value=False):
            self.assertEqual(self.confirm(token).status_code,503)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.two_factor_enabled)
        self.assertTrue(self.owner.check_password(TEST_PASSWORD))
        self.assertIsNone(self.case().completed_at)
