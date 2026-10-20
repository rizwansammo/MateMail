"""Stage 1: PostBox 2FA recovery-code rotation security acceptance."""
from unittest import mock

import pyotp
from cryptography.fernet import Fernet
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.postbox import auth as postbox_auth
from apps.postbox.models import (
    MailboxTwoFactorBackupCode, MailboxTwoFactorSetup, PostBoxSession,
)
from apps.postbox.two_factor_core import (
    encrypt_totp_secret, hash_backup_code, redeem_backup_code,
)
from tests.factories import FAST_PASSWORD_HASHERS, make_domain, make_mailbox, make_tenant, make_user

CACHE = {"default": {
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "postbox-stage1-backup-rotation-tests",
}}
TEST_KEY = Fernet.generate_key().decode("ascii")
ENDPOINT = "/api/postbox/auth/2fa/backup-codes/regenerate/"


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=CACHE,
    POSTBOX_2FA_ENABLED=True,
    SMTP_IMAP_SECURITY_ENABLED=True,
    MAIL_ENGINE_ADAPTER="native",
    POSTBOX_2FA_FERNET_KEY=TEST_KEY,
)
class PostBoxBackupRotationTest(TestCase):
    def setUp(self):
        cache.clear()
        owner = make_user("stage1-owner@example.test")
        tenant = make_tenant(owner, "Stage 1 Security", "stage1-rotation")
        domain = make_domain(tenant, "stage1-rotation.example.test")
        self.mailbox = make_mailbox(tenant, domain, "security")
        self.mailbox.mail_engine_provisioned = True
        self.mailbox.save(update_fields=["mail_engine_provisioned"])
        self.secret = pyotp.random_base32()
        MailboxTwoFactorSetup.objects.create(
            mailbox=self.mailbox, enabled_at=timezone.now(),
            totp_secret_encrypted=encrypt_totp_secret(self.secret),
        )
        self.old_code = "old-backup-valid-random-looking-123456"
        MailboxTwoFactorBackupCode.objects.create(
            mailbox=self.mailbox, code_hash=hash_backup_code(self.old_code),
        )
        self.client = APIClient()
        self.session_token, self.session = PostBoxSession.issue(
            self.mailbox, mfa_verified=True
        )
        self.client.cookies[postbox_auth.SESSION_COOKIE_NAME] = self.session_token

    def submit(self, password="mailbox-password", code=None):
        return self.client.post(ENDPOINT, {
            "current_password": password,
            "code": code or pyotp.TOTP(self.secret).now(),
        }, format="json")

    def test_rotation_replaces_every_old_code_and_new_codes_are_single_use(self):
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate", return_value=True):
            response = self.submit()
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        codes = response.data["backup_codes"]
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        self.assertNotIn(self.old_code, codes)
        self.assertEqual(MailboxTwoFactorBackupCode.objects.filter(mailbox=self.mailbox).count(), 10)
        self.assertFalse(redeem_backup_code(self.mailbox, self.old_code))
        self.assertTrue(redeem_backup_code(self.mailbox, codes[0]))
        self.assertFalse(redeem_backup_code(self.mailbox, codes[0]))
        self.assertFalse(MailboxTwoFactorBackupCode.objects.filter(
            mailbox=self.mailbox, code_hash=codes[1]
        ).exists())
        self.assertTrue(PostBoxSession.objects.get(pk=self.session.pk).is_active)

    def test_password_must_be_reverified_before_mutation(self):
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate", return_value=False):
            response = self.submit(password="wrong")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(redeem_backup_code(self.mailbox, self.old_code))

    def test_totp_replay_cannot_mint_second_set(self):
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate", return_value=True):
            first = self.submit()
            second = self.submit()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 403)
        self.assertEqual(MailboxTwoFactorBackupCode.objects.filter(mailbox=self.mailbox).count(), 10)
        self.assertTrue(redeem_backup_code(self.mailbox, first.data["backup_codes"][0]))

    def test_backup_code_cannot_authorize_rotation_in_place_of_totp(self):
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate", return_value=True):
            response = self.submit(code=self.old_code)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(redeem_backup_code(self.mailbox, self.old_code))

    def test_no_session_cannot_rotate_backup_codes(self):
        anonymous = APIClient()
        response = anonymous.post(ENDPOINT, {
            "current_password": "password", "code": pyotp.TOTP(self.secret).now(),
        }, format="json")
        self.assertIn(response.status_code, (401, 403))

    def test_rollout_flag_off_rejects_rotation_without_mutation(self):
        with override_settings(POSTBOX_2FA_ENABLED=False):
            response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertTrue(redeem_backup_code(self.mailbox, self.old_code))

    def test_password_change_revokes_pending_mfa_challenges(self):
        from apps.postbox import two_factor_challenge as challenges
        prior = challenges.create_challenge(self.mailbox)
        self.assertIsNotNone(challenges.peek_challenge(prior))
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate", return_value=True), \
             mock.patch("apps.mail_engine.factory.get_adapter") as adapter:
            response = self.client.post("/api/postbox/security/change-password/", {
                "current_password": "mailbox-password",
                "new_password": "New-Random-Mailbox-Passphrase-987!",
            }, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertIsNone(challenges.peek_challenge(prior))
        self.assertFalse(challenges.consume_challenge(prior))
        fresh = challenges.create_challenge(self.mailbox)
        self.assertIsNotNone(challenges.peek_challenge(fresh))
        adapter.return_value.set_mailbox_password.assert_called_once()
