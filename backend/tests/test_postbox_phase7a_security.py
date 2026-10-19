"""Phase 7A PostBox web/client authentication boundary contracts."""
from unittest import mock

import pyotp
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.postbox.models import (
    MailboxTwoFactorSetup, PostBoxSession, MailboxTwoFactorBackupCode,
)
from apps.postbox.two_factor_core import encrypt_totp_secret
from apps.postbox import auth as postbox_auth
from tests.factories import FAST_PASSWORD_HASHERS, make_user, make_tenant, make_domain, make_mailbox

LOCMEM = {"default": {
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "phase7a-postbox-mfa-acceptance",
}}
TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM,
    POSTBOX_2FA_ENABLED=True,
    SMTP_IMAP_SECURITY_ENABLED=True,
    MAIL_ENGINE_ADAPTER="native",
    POSTBOX_2FA_FERNET_KEY=TEST_ENCRYPTION_KEY,
)
class PostBoxMfaBoundaryTest(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        owner = make_user("pb-owner@example.test")
        self.tenant = make_tenant(owner, "PB MFA", "pb-mfa")
        domain = make_domain(self.tenant, "pbmfa.example.test")
        self.mailbox = make_mailbox(self.tenant, domain, "alice")
        self.mailbox.mail_engine_provisioned = True
        self.mailbox.save(update_fields=["mail_engine_provisioned"])
        self.client = APIClient()

    def _enroll_directly(self):
        secret = pyotp.random_base32()
        MailboxTwoFactorSetup.objects.create(
            mailbox=self.mailbox,
            totp_secret_encrypted=encrypt_totp_secret(secret),
            enabled_at=__import__("django.utils.timezone",fromlist=["now"]).now(),
        )
        return secret

    def _login(self):
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate", return_value=True):
            return self.client.post("/api/postbox/auth/login/", {
                "email": self.mailbox.email, "password": "test-mailbox-pass",
            }, format="json")

    def test_mfa_requires_challenge_without_issuing_browser_session(self):
        self._enroll_directly()
        r = self._login()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["requires_2fa"])
        self.assertNotIn("session", r.data)
        self.assertFalse(PostBoxSession.objects.filter(mailbox=self.mailbox).exists())

    def test_verified_mfa_creates_proven_session(self):
        secret = self._enroll_directly()
        response = self._login()
        code = pyotp.TOTP(secret).now()
        with mock.patch("apps.postbox.views_auth.postbox_auth.remember_session_on_device"), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            verified = self.client.post("/api/postbox/auth/2fa/verify/", {
                "partial_token": response.data["partial_token"], "code": code,
            }, format="json")
        self.assertEqual(verified.status_code, 200, verified.data)
        session = PostBoxSession.objects.get(mailbox=self.mailbox)
        self.assertIsNotNone(session.mfa_verified_at)

    def test_old_session_is_denied_after_enforcement(self):
        self._enroll_directly()
        token, session = PostBoxSession.issue(self.mailbox)
        self.assertIsNone(postbox_auth.session_for_token(token))
        self.assertIsNone(session.mfa_verified_at)


    def _require_mfa_policy(self):
        self.tenant.two_factor_policy = "require"
        self.tenant.save(update_fields=["two_factor_policy"])
        return override_settings(
            ORGANIZATION_2FA_POLICY_ENABLED=True,
            ORGANIZATION_2FA_ENFORCEMENT_READY=True,
        )

    def test_require_policy_blocks_password_only_postbox_and_existing_session(self):
        raw, _ = PostBoxSession.issue(self.mailbox)
        with self._require_mfa_policy():
            self.assertIsNone(postbox_auth.session_for_token(raw))
            response = self._login()
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(response.data["requires_2fa"])
            self.assertTrue(response.data["requires_enrollment"])
            self.assertNotIn("session", response.data)
            self.assertEqual(PostBoxSession.objects.filter(
                mailbox=self.mailbox, mfa_verified_at__isnull=False,
            ).count(), 0)

    def test_require_policy_enrolls_only_after_native_security_ack_and_denies_disable(self):
        with self._require_mfa_policy():
            login = self._login()
            self.assertTrue(login.data["requires_enrollment"])
            partial = login.data["partial_token"]
            no_setup = self.client.post("/api/postbox/auth/2fa/verify/", {
                "partial_token": partial, "code": "123456",
            }, format="json")
            self.assertEqual(no_setup.status_code, 409)
            begin = self.client.post("/api/postbox/auth/2fa/enroll/", {
                "partial_token": partial, "action": "begin",
            }, format="json")
            self.assertEqual(begin.status_code, 200, begin.data)

            adapter = mock.Mock()
            adapter.list_client_credentials.return_value = {"enabled": True}
            adapter.issue_client_credential.return_value = {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "one_time_credential": "synthetic-mfa-client-only",
            }
            adapter.set_client_policy.return_value = {
                "enabled": True, "dedicated_required": True,
            }
            with mock.patch("apps.postbox.two_factor_views.get_adapter", return_value=adapter), \
                 mock.patch("apps.postbox.views_auth.postbox_auth.remember_session_on_device"), \
                 mock.patch("apps.postbox.imap.open_mailbox"):
                finish = self.client.post("/api/postbox/auth/2fa/enroll/", {
                    "partial_token": partial, "action": "confirm",
                    "code": pyotp.TOTP(begin.data["secret"]).now(),
                }, format="json")
            self.assertEqual(finish.status_code, 200, finish.data)
            adapter.set_client_policy.assert_called_once_with(
                self.mailbox.email, enabled=True, dedicated_required=True,
            )
            self.assertTrue(PostBoxSession.objects.filter(
                mailbox=self.mailbox, mfa_verified_at__isnull=False,
            ).exists())
            denial = self.client.post("/api/postbox/auth/2fa/disable/", {
                "current_password": "anything", "code": "123456",
            }, format="json")
            self.assertEqual(denial.status_code, 409)
            self.assertTrue(MailboxTwoFactorSetup.objects.filter(
                mailbox=self.mailbox, enabled_at__isnull=False,
            ).exists())

    def test_require_policy_enrollment_native_outage_leaves_mailbox_locked(self):
        with self._require_mfa_policy():
            login = self._login()
            partial = login.data["partial_token"]
            begin = self.client.post("/api/postbox/auth/2fa/enroll/", {
                "partial_token": partial, "action": "begin",
            }, format="json")
            adapter = mock.Mock()
            adapter.list_client_credentials.side_effect = RuntimeError("isolated native outage")
            with mock.patch("apps.postbox.two_factor_views.get_adapter", return_value=adapter):
                refused = self.client.post("/api/postbox/auth/2fa/enroll/", {
                    "partial_token": partial, "action": "confirm",
                    "code": pyotp.TOTP(begin.data["secret"]).now(),
                }, format="json")
            self.assertEqual(refused.status_code, 503)
            self.assertFalse(PostBoxSession.objects.filter(
                mailbox=self.mailbox, mfa_verified_at__isnull=False,
            ).exists())
            self.assertIsNone(MailboxTwoFactorSetup.objects.get(
                mailbox=self.mailbox,
            ).enabled_at)
            self.assertTrue(self._login().data["requires_enrollment"])

    def test_mfa_primary_verifier_does_not_fall_back_to_public_imap(self):
        adapter = mock.Mock()
        adapter.verify_primary_password.return_value = False
        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter), \
             mock.patch("apps.postbox.auth.imap.authenticate") as public_imap:
            ok = postbox_auth.trusted_primary_authenticate(
                self.mailbox.email, "normal-pass",
            )
        self.assertFalse(ok)
        public_imap.assert_not_called()

    def test_enroll_creates_initial_client_password_only_after_totp(self):
        self._login()
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate", return_value=True):
            start = self.client.post("/api/postbox/auth/2fa/start/", {
                "current_password": "test-mailbox-pass",
            }, format="json")
        self.assertEqual(start.status_code, 200, start.data)
        self.assertTrue(start.data["requires_enrollment"])
        raw = start.data["partial_token"]
        begin = self.client.post("/api/postbox/auth/2fa/enroll/", {
            "partial_token": raw, "action": "begin",
        }, format="json")
        self.assertEqual(begin.status_code, 200, begin.data)
        secret = begin.data["secret"]
        adapter = mock.Mock()
        adapter.list_client_credentials.return_value = {"enabled": True}
        adapter.issue_client_credential.return_value = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "one_time_credential": "test-generated-client-password",
        }
        adapter.set_client_policy.return_value = {
            "enabled": True, "dedicated_required": True,
        }
        with mock.patch("apps.postbox.two_factor_views.get_adapter", return_value=adapter), \
             mock.patch("apps.postbox.views_auth.postbox_auth.remember_session_on_device"), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            finish = self.client.post("/api/postbox/auth/2fa/enroll/", {
                "partial_token": raw, "action": "confirm",
                "code": pyotp.TOTP(secret).now(),
            }, format="json")
        self.assertEqual(finish.status_code, 200, finish.data)
        self.assertEqual(finish.data["smtp_imap_password"], "test-generated-client-password")
        self.assertEqual(len(finish.data["backup_codes"]), 10)
        self.assertEqual(MailboxTwoFactorBackupCode.objects.filter(mailbox=self.mailbox).count(), 10)
        adapter.set_client_policy.assert_called_once_with(
            self.mailbox.email, enabled=True, dedicated_required=True,
        )
        setup = MailboxTwoFactorSetup.objects.get(mailbox=self.mailbox)
        self.assertIsNotNone(setup.enabled_at)

    def test_disable_orders_native_revocation_before_policy_relaxation(self):
        self._enroll_directly()
        raw, session = PostBoxSession.issue(self.mailbox, mfa_verified=True)
        self.client.cookies[postbox_auth.SESSION_COOKIE_NAME] = raw
        calls = []
        adapter = mock.Mock()
        adapter.list_client_credentials.return_value = {
            "enabled": True, "credentials": [
                {"id": "123e4567-e89b-12d3-a456-426614174000",
                 "revoked_at": None},
            ],
        }
        adapter.revoke_client_credential.side_effect = (
            lambda *args: calls.append("revoke")
        )
        adapter.set_client_policy.side_effect = (
            lambda *args, **kwargs: calls.append("relax")
        )
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate",
                               return_value=True), \
             mock.patch("apps.postbox.two_factor_views.verify_totp",
                        return_value=True), \
             mock.patch("apps.postbox.two_factor_views.get_adapter",
                        return_value=adapter):
            result = self.client.post("/api/postbox/auth/2fa/disable/", {
                "current_password": "test-mailbox-pass", "code": "123456",
            }, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(calls, ["revoke", "relax"])
        self.assertFalse(MailboxTwoFactorSetup.objects.filter(
            mailbox=self.mailbox).exists())

    def test_disable_never_relaxes_policy_if_revocation_fails(self):
        self._enroll_directly()
        raw, session = PostBoxSession.issue(self.mailbox, mfa_verified=True)
        self.client.cookies[postbox_auth.SESSION_COOKIE_NAME] = raw
        adapter = mock.Mock()
        adapter.list_client_credentials.return_value = {
            "enabled": True, "credentials": [
                {"id": "123e4567-e89b-12d3-a456-426614174000",
                 "revoked_at": None},
            ],
        }
        adapter.revoke_client_credential.side_effect = RuntimeError("unavailable")
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate",
                               return_value=True), \
             mock.patch("apps.postbox.two_factor_views.verify_totp",
                        return_value=True), \
             mock.patch("apps.postbox.two_factor_views.get_adapter",
                        return_value=adapter):
            result = self.client.post("/api/postbox/auth/2fa/disable/", {
                "current_password": "test-mailbox-pass", "code": "123456",
            }, format="json")
        self.assertEqual(result.status_code, 503)
        self.assertTrue(MailboxTwoFactorSetup.objects.filter(
            mailbox=self.mailbox).exists())
        adapter.set_client_policy.assert_not_called()

    def test_enrollment_aborts_if_native_enforcement_unavailable(self):
        self._login()
        with mock.patch.object(postbox_auth, "trusted_primary_authenticate", return_value=True):
            start = self.client.post("/api/postbox/auth/2fa/start/", {
                "current_password": "test-mailbox-pass",
            }, format="json")
        self.assertEqual(start.status_code, 200, start.data)
        raw = start.data["partial_token"]
        begin = self.client.post("/api/postbox/auth/2fa/enroll/", {
            "partial_token": raw, "action": "begin",
        }, format="json")
        adapter = mock.Mock()
        adapter.list_client_credentials.side_effect = RuntimeError("Native is unavailable")
        with mock.patch("apps.postbox.two_factor_views.get_adapter", return_value=adapter):
            result = self.client.post("/api/postbox/auth/2fa/enroll/", {
                "partial_token": raw, "action": "confirm",
                "code": pyotp.TOTP(begin.data["secret"]).now(),
            }, format="json")
        self.assertEqual(result.status_code, 503)
        self.assertIsNone(MailboxTwoFactorSetup.objects.get(mailbox=self.mailbox).enabled_at)
