"""Encrypted mailbox OTP and high-entropy recovery primitives, no HTTP enrollment."""
import pyotp
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.postbox.models import MailboxTwoFactorBackupCode, MailboxTwoFactorSetup
from apps.postbox.two_factor_core import (
    decrypt_totp_secret, encrypt_totp_secret, enrollment_ready,
    generate_backup_codes, hash_backup_code, redeem_backup_code, verify_totp,
)
from tests.factories import make_domain, make_mailbox, make_tenant, make_user

TEST_KEY = Fernet.generate_key().decode("ascii")
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                      "LOCATION": "phase7a-mailbox-mfa"}}


@override_settings(POSTBOX_2FA_FERNET_KEY=TEST_KEY, CACHES=LOCMEM)
class MailboxTwoFactorCryptoTest(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.owner = make_user("mfa-owner@example.test")
        self.tenant = make_tenant(self.owner, "MFA Test", "mfa-test")
        self.domain = make_domain(self.tenant, "mfa-test.example.test")
        self.mailbox = make_mailbox(self.tenant, self.domain, "alice")

    def test_ciphertext_is_distinct_and_roundtrips(self):
        secret = pyotp.random_base32()
        ciphertext = encrypt_totp_secret(secret)
        self.assertNotIn(secret, ciphertext)
        self.assertEqual(decrypt_totp_secret(ciphertext), secret)

    def test_key_required_not_silently_downgraded(self):
        secret = pyotp.random_base32()
        ciphertext = encrypt_totp_secret(secret)
        with override_settings(POSTBOX_2FA_FERNET_KEY=""):
            with self.assertRaises(ImproperlyConfigured):
                decrypt_totp_secret(ciphertext)
        with override_settings(POSTBOX_2FA_FERNET_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(ValueError):
                decrypt_totp_secret(ciphertext)

    def test_setup_encrypted_at_rest_and_pending_does_not_authenticate(self):
        secret = pyotp.random_base32()
        setup = MailboxTwoFactorSetup.objects.create(
            mailbox=self.mailbox,
            totp_secret_encrypted=encrypt_totp_secret(secret),
        )
        self.assertIsNone(setup.enabled_at)
        self.assertFalse(verify_totp(setup, pyotp.TOTP(secret).now()))

    def test_enabled_totp_cannot_be_replayed(self):
        secret = pyotp.random_base32()
        setup = MailboxTwoFactorSetup.objects.create(
            mailbox=self.mailbox,
            totp_secret_encrypted=encrypt_totp_secret(secret),
            enabled_at=timezone.now(),
        )
        code = pyotp.TOTP(secret).now()
        self.assertTrue(verify_totp(setup, code))
        self.assertFalse(verify_totp(setup, code))
        self.assertFalse(verify_totp(setup, "00000x"))

    def test_backup_code_one_time_and_not_stored_raw(self):
        raw = generate_backup_codes(1)[0]
        digest = hash_backup_code(raw)
        self.assertNotIn(raw, digest)
        MailboxTwoFactorBackupCode.objects.create(
            mailbox=self.mailbox, code_hash=digest,
        )
        self.assertTrue(redeem_backup_code(self.mailbox, raw))
        self.assertFalse(redeem_backup_code(self.mailbox, raw))

    def test_recovery_is_mailbox_scoped(self):
        other = make_mailbox(self.tenant, self.domain, "bob")
        raw = generate_backup_codes(1)[0]
        MailboxTwoFactorBackupCode.objects.create(
            mailbox=self.mailbox, code_hash=hash_backup_code(raw),
        )
        self.assertFalse(redeem_backup_code(other, raw))
        self.assertTrue(redeem_backup_code(self.mailbox, raw))

    def test_enrollment_readiness_single_source_of_truth(self):
        from apps.postbox.two_factor_views import _ready
        cases = (
            ({"POSTBOX_2FA_ENABLED": False, "SMTP_IMAP_SECURITY_ENABLED": False,
              "MAIL_ENGINE_ADAPTER": "native"}, False),
            ({"POSTBOX_2FA_ENABLED": True, "SMTP_IMAP_SECURITY_ENABLED": False,
              "MAIL_ENGINE_ADAPTER": "native"}, False),
            ({"POSTBOX_2FA_ENABLED": False, "SMTP_IMAP_SECURITY_ENABLED": True,
              "MAIL_ENGINE_ADAPTER": "native"}, False),
            ({"POSTBOX_2FA_ENABLED": True, "SMTP_IMAP_SECURITY_ENABLED": True,
              "MAIL_ENGINE_ADAPTER": "stub"}, False),
            ({"POSTBOX_2FA_ENABLED": True, "SMTP_IMAP_SECURITY_ENABLED": True,
              "MAIL_ENGINE_ADAPTER": "native"}, True),
        )
        for overrides, expected in cases:
            with self.subTest(config=overrides), override_settings(**overrides):
                self.assertIs(enrollment_ready(), expected)
                self.assertIs(_ready(), expected)

    def test_invalid_encryption_key_blocks_all_postbox_mfa_routes(self):
        from apps.postbox.two_factor_views import _ready
        from rest_framework.test import APIClient
        for bad_key in ("", "not-a-valid-fernet-key", "é-invalid-encoding"):
            with self.subTest(bad_key=bad_key), override_settings(
                POSTBOX_2FA_ENABLED=True,
                SMTP_IMAP_SECURITY_ENABLED=True,
                MAIL_ENGINE_ADAPTER="native",
                POSTBOX_2FA_FERNET_KEY=bad_key,
            ):
                self.assertFalse(enrollment_ready())
                self.assertFalse(_ready())
                response = APIClient().post("/api/postbox/auth/2fa/enroll/", {}, format="json")
                self.assertEqual(response.status_code, 503, response.data)
                self.assertNotIn("secret", response.data)

    def test_require_mfa_remains_independently_release_locked(self):
        from apps.tenants.security_policy import can_activate_require
        with override_settings(
            POSTBOX_2FA_ENABLED=True,
            SMTP_IMAP_SECURITY_ENABLED=True,
            MAIL_ENGINE_ADAPTER="native",
            ORGANIZATION_2FA_POLICY_ENABLED=True,
            ORGANIZATION_2FA_ENFORCEMENT_READY=True,
        ):
            self.assertTrue(enrollment_ready())
            self.assertFalse(can_activate_require())
