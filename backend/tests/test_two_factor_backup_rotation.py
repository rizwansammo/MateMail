"""Hub 2FA recovery-code rotation: security and transactional regression tests."""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import TwoFactorBackupCode
from apps.accounts.tokens import hash_token
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    auth_client,
    disable_throttling,
    enable_2fa,
    make_tenant,
    make_user,
    totp_now,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "hub-2fa-code-rotation-test",
    }
}
ENDPOINT = "/api/auth/2fa/backup-codes/regenerate/"


@override_settings(CACHES=LOCMEM_CACHE, PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class HubBackupCodeRotationTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("rotation@example.test", full_name="Recovery Tester")
        self.tenant = make_tenant(self.user, name="Rotation Test", slug="rotation-test")
        self.secret = enable_2fa(self.user)
        self.client = auth_client(self.user, self.tenant)
        self.old_codes = ["OLDTEST1", "OLDTEST2"]
        TwoFactorBackupCode.objects.bulk_create([
            TwoFactorBackupCode(user=self.user, code_hash=hash_token(value))
            for value in self.old_codes
        ])

    def rotate(self, password=TEST_PASSWORD, code=None):
        return self.client.post(
            ENDPOINT,
            {"password": password, "code": code if code is not None else totp_now(self.secret)},
            format="json",
        )

    def test_unauthenticated_cannot_rotate(self):
        response = APIClient().post(
            ENDPOINT, {"password": TEST_PASSWORD, "code": totp_now(self.secret)}, format="json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(TwoFactorBackupCode.objects.filter(user=self.user).count(), 2)

    def test_cannot_rotate_if_mfa_disabled(self):
        self.user.two_factor_enabled = False
        self.user.save(update_fields=["two_factor_enabled"])
        response = self.rotate()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(TwoFactorBackupCode.objects.filter(user=self.user).count(), 2)

    def test_wrong_password_does_not_revoke_existing_codes(self):
        response = self.rotate(password="wrong-password")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            list(TwoFactorBackupCode.objects.filter(user=self.user).values_list("code_hash", flat=True)),
            [hash_token(value) for value in self.old_codes],
        )

    def test_wrong_or_malformed_totp_does_not_revoke_codes(self):
        wrong = "000000"
        if wrong == totp_now(self.secret):
            wrong = "999999"
        response = self.rotate(code=wrong)
        self.assertEqual(response.status_code, 403)
        response = self.rotate(code="not-an-otp")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(TwoFactorBackupCode.objects.filter(user=self.user).count(), 2)

    def test_success_replaces_all_codes_and_never_stores_plaintext(self):
        response = self.rotate()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store, private")
        codes = response.data["backup_codes"]
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        self.assertTrue(all(len(c) == 8 for c in codes))
        stored = set(TwoFactorBackupCode.objects.filter(user=self.user).values_list("code_hash", flat=True))
        self.assertEqual(stored, {hash_token(value) for value in codes})
        self.assertFalse({hash_token(value) for value in self.old_codes} & stored)
        self.assertFalse(any(code in stored for code in codes))
        self.user.refresh_from_db()
        self.assertTrue(self.user.two_factor_enabled)

    def test_used_totp_cannot_rotate_again(self):
        code = totp_now(self.secret)
        first = self.rotate(code=code)
        self.assertEqual(first.status_code, 200)
        hashes = set(TwoFactorBackupCode.objects.filter(user=self.user).values_list("code_hash", flat=True))
        second = self.rotate(code=code)
        self.assertEqual(second.status_code, 403)
        self.assertEqual(
            set(TwoFactorBackupCode.objects.filter(user=self.user).values_list("code_hash", flat=True)),
            hashes,
        )

    def test_old_code_cannot_complete_login_after_rotation_new_code_can(self):
        response = self.rotate()
        self.assertEqual(response.status_code, 200)
        fresh = response.data["backup_codes"][0]

        def challenge():
            login = APIClient().post(
                "/api/auth/login/",
                {"email": self.user.email, "password": TEST_PASSWORD},
                format="json",
            )
            self.assertEqual(login.status_code, 200)
            return login.data["partial_token"]

        old = APIClient().post(
            "/api/auth/2fa/verify/",
            {"partial_token": challenge(), "code": self.old_codes[0]},
            format="json",
        )
        self.assertEqual(old.status_code, 401)
        new = APIClient().post(
            "/api/auth/2fa/verify/",
            {"partial_token": challenge(), "code": fresh},
            format="json",
        )
        self.assertEqual(new.status_code, 200)
        self.assertIn("access", new.data)

    def test_transaction_failure_restores_old_codes(self):
        with mock.patch(
            "apps.accounts.views.TwoFactorBackupCode.objects.bulk_create",
            side_effect=RuntimeError("simulated database write failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.rotate()
        self.assertEqual(
            set(TwoFactorBackupCode.objects.filter(user=self.user).values_list("code_hash", flat=True)),
            {hash_token(value) for value in self.old_codes},
        )
