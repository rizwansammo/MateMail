"""Independent recovery address must be verified before it can bypass TOTP."""
import re
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import VerifiedRecoveryEmail
from apps.accounts.totp_crypto import decrypt_secret
from tests.factories import FAST_PASSWORD_HASHERS, TEST_PASSWORD, auth_client, enable_2fa, make_tenant, make_user

LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "phase7a-recovery-email-tests",
    }
}


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM,
    HUB_2FA_FERNET_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
)
class VerifiedRecoveryEmailTest(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = make_user("recovery-owner@example.test")
        self.tenant = make_tenant(self.user, name="Recovery", slug="recovery")
        self.client = auth_client(self.user, self.tenant)

    def test_primary_address_is_not_allowed_as_recovery(self):
        result = self.client.post("/api/auth/recovery-email/", {
            "recovery_email": self.user.email,
            "current_password": TEST_PASSWORD,
        }, format="json")
        self.assertEqual(result.status_code, 400)
        self.assertFalse(VerifiedRecoveryEmail.objects.filter(user=self.user).exists())

    def test_pending_email_cannot_be_used_as_recovery(self):
        with mock.patch("apps.accounts.recovery_views.send_transactional", return_value=True):
            start = self.client.post("/api/auth/recovery-email/", {
                "recovery_email": "other@example.net",
                "current_password": TEST_PASSWORD,
            }, format="json")
        self.assertEqual(start.status_code, 200, start.data)
        contact = VerifiedRecoveryEmail.objects.get(user=self.user)
        self.assertIsNone(contact.verified_at)
        self.assertEqual(contact.email, "")
        self.assertEqual(contact.pending_email, "other@example.net")

    def test_verification_and_recovery_one_time(self):
        sent = []
        def capture(**data):
            sent.append(data)
            return True
        with mock.patch("apps.accounts.recovery_views.send_transactional", side_effect=capture):
            started = self.client.post("/api/auth/recovery-email/", {
                "recovery_email": "other@example.net",
                "current_password": TEST_PASSWORD,
            }, format="json")
        self.assertEqual(started.status_code, 200, started.data)
        code = re.search(r"\b\d{6}\b", sent[0]["body"]).group()
        verified = self.client.post("/api/auth/recovery-email/confirm/", {
            "challenge": started.data["challenge"], "code": code,
        }, format="json")
        self.assertEqual(verified.status_code, 200, verified.data)
        self.assertTrue(VerifiedRecoveryEmail.objects.get(user=self.user).verified_at)
        replay = self.client.post("/api/auth/recovery-email/confirm/", {
            "challenge": started.data["challenge"], "code": code,
        }, format="json")
        self.assertEqual(replay.status_code, 401)

        secret = enable_2fa(self.user)
        login = APIClient().post("/api/auth/login/", {
            "email": self.user.email, "password": TEST_PASSWORD,
        }, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        self.assertTrue(login.data["requires_2fa"])
        sent.clear()
        with mock.patch("apps.accounts.recovery_views.send_transactional", side_effect=capture):
            start = APIClient().post("/api/auth/2fa/recovery/start/", {
                "partial_token": login.data["partial_token"],
            }, format="json")
        self.assertEqual(start.status_code, 200, start.data)
        code = re.search(r"\b\d{6}\b", sent[0]["body"]).group()
        finish = APIClient().post("/api/auth/2fa/recovery/finish/", {
            "partial_token": login.data["partial_token"],
            "recovery_challenge": start.data["recovery_challenge"],
            "code": code,
        }, format="json")
        self.assertEqual(finish.status_code, 200, finish.data)
        self.assertIn("access", finish.data)
        replay = APIClient().post("/api/auth/2fa/recovery/finish/", {
            "partial_token": login.data["partial_token"],
            "recovery_challenge": start.data["recovery_challenge"],
            "code": code,
        }, format="json")
        self.assertEqual(replay.status_code, 401)
