"""Stage 2 scoped Canary: non-canary users retain their legacy mail login.

Production-facing Release Gates remain OFF until operator-approved deployment.
These tests are deterministic: Native API and IMAP are mocked at their
transport boundaries; no customer mailboxes or credentials are used.
"""
from unittest import mock

import pyotp
from cryptography.fernet import Fernet
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.postbox import auth as postbox_auth
from apps.postbox.canary_scope import (
    can_start_mfa_enrollment, mailbox_in_mfa_scope,
)
from apps.postbox.models import MailboxTwoFactorSetup, PostBoxSession
from apps.postbox.two_factor_core import encrypt_totp_secret
from tests.factories import (
    FAST_PASSWORD_HASHERS, make_domain, make_mailbox, make_tenant, make_user,
)

ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
CACHE = {"default": {
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "postbox-canary-acceptance-stage2",
}}


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=CACHE,
    POSTBOX_2FA_ENABLED=True,
    SMTP_IMAP_SECURITY_ENABLED=True,
    POSTBOX_2FA_CANARY_ENABLED=True,
    POSTBOX_2FA_CANARY_MAILBOX_IDS="",
    MAIL_ENGINE_ADAPTER="native",
    POSTBOX_2FA_FERNET_KEY=ENCRYPTION_KEY,
    ORGANIZATION_2FA_POLICY_ENABLED=False,
    ORGANIZATION_2FA_ENFORCEMENT_READY=False,
)
class PostBoxCanaryIsolationTest(TestCase):
    def setUp(self):
        cache.clear()
        owner = make_user("canary-owner@example.test")
        self.tenant = make_tenant(owner, "Scoped Acceptance", "stage2-canary")
        domain = make_domain(self.tenant, "canary.example.test")
        self.canary = make_mailbox(self.tenant, domain, "canary")
        self.control = make_mailbox(self.tenant, domain, "control")
        for mailbox in (self.canary, self.control):
            mailbox.mail_engine_provisioned = True
            mailbox.save(update_fields=["mail_engine_provisioned"])

    def test_scope_empty_or_invalid_allowlist_selects_nobody(self):
        for raw in ("", "not-a-uuid", ",bad, ,invalid,"):
            with self.subTest(allowlist=raw), override_settings(
                POSTBOX_2FA_CANARY_MAILBOX_IDS=raw,
            ):
                self.assertFalse(can_start_mfa_enrollment(self.canary))
                self.assertFalse(mailbox_in_mfa_scope(self.canary))
                self.assertFalse(can_start_mfa_enrollment(self.control))

    def test_canary_uuid_scopes_only_selected_mailbox(self):
        with override_settings(
            POSTBOX_2FA_CANARY_MAILBOX_IDS=str(self.canary.pk),
        ):
            self.assertTrue(can_start_mfa_enrollment(self.canary))
            self.assertFalse(can_start_mfa_enrollment(self.control))
            self.assertTrue(mailbox_in_mfa_scope(self.canary))
            self.assertFalse(mailbox_in_mfa_scope(self.control))

    def test_non_canary_uses_existing_imap_verification_and_canary_uses_native(self):
        with override_settings(POSTBOX_2FA_CANARY_MAILBOX_IDS=str(self.canary.pk)), \
                mock.patch.object(postbox_auth.imap, "authenticate", return_value=True) as old_imap, \
                mock.patch("apps.mail_engine.factory.get_adapter") as factory:
            factory.return_value.verify_primary_password.return_value = True

            self.assertTrue(postbox_auth.trusted_primary_authenticate(
                self.control.email, "synthetic-primary",
            ))
            old_imap.assert_called_once_with(self.control.email, "synthetic-primary")
            factory.assert_not_called()

            old_imap.reset_mock()
            self.assertTrue(postbox_auth.trusted_primary_authenticate(
                self.canary.email, "synthetic-primary",
            ))
            factory.return_value.verify_primary_password.assert_called_once_with(
                self.canary.email, "synthetic-primary",
            )
            old_imap.assert_not_called()

    def test_non_canary_real_postbox_http_login_retains_password_only_session(self):
        with override_settings(POSTBOX_2FA_CANARY_MAILBOX_IDS=str(self.canary.pk)), \
                mock.patch.object(postbox_auth.imap, "authenticate", return_value=True), \
                mock.patch("apps.mail_engine.factory.get_adapter") as factory, \
                mock.patch("apps.postbox.imap.open_mailbox"):
            response = APIClient().post(
                "/api/postbox/auth/login/",
                {"email": self.control.email, "password": "synthetic-password"},
                format="json",
            )
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertIn("session", response.data)
        self.assertNotIn("partial_token", response.data)
        factory.assert_not_called()
        self.assertTrue(PostBoxSession.objects.filter(mailbox=self.control).exists())

    def test_outside_scope_cannot_start_enrollment_or_manage_device_password(self):
        raw, _ = PostBoxSession.issue(self.control)
        client = APIClient()
        client.cookies[postbox_auth.SESSION_COOKIE_NAME] = raw
        with override_settings(POSTBOX_2FA_CANARY_MAILBOX_IDS=str(self.canary.pk)), \
                mock.patch.object(postbox_auth, "trusted_primary_authenticate") as verifier, \
                mock.patch("apps.postbox.two_factor_views.get_adapter") as adapter:
            started = client.post(
                "/api/postbox/auth/2fa/start/",
                {"current_password": "synthetic-password"},
                format="json",
            )
            device = client.get("/api/postbox/security/smtp-imap/")
            created = client.post(
                "/api/postbox/security/smtp-imap/",
                {"current_password": "synthetic-password", "action": "create",
                 "label": "Synthetic external client"},
                format="json",
            )
        self.assertEqual(started.status_code, 503)
        self.assertEqual(device.status_code, 503)
        self.assertEqual(created.status_code, 503)
        verifier.assert_not_called()
        adapter.assert_not_called()

    def test_canary_can_begin_enrollment_but_has_no_forced_mfa_before_enrolling(self):
        raw, _ = PostBoxSession.issue(self.canary)
        client = APIClient()
        client.cookies[postbox_auth.SESSION_COOKIE_NAME] = raw
        with override_settings(POSTBOX_2FA_CANARY_MAILBOX_IDS=str(self.canary.pk)), \
                mock.patch.object(postbox_auth, "trusted_primary_authenticate", return_value=True):
            started = client.post(
                "/api/postbox/auth/2fa/start/",
                {"current_password": "synthetic-password"}, format="json",
            )
        self.assertEqual(started.status_code, 200, getattr(started, "data", None))
        self.assertTrue(started.data["requires_enrollment"])
        self.assertIsNotNone(postbox_auth.session_for_token(raw))

    def test_removing_mailbox_from_canary_never_bypasses_its_existing_mfa(self):
        secret = pyotp.random_base32()
        MailboxTwoFactorSetup.objects.create(
            mailbox=self.canary, enabled_at=timezone.now(),
            totp_secret_encrypted=encrypt_totp_secret(secret),
        )
        old_raw, _ = PostBoxSession.issue(self.canary)
        with override_settings(POSTBOX_2FA_CANARY_MAILBOX_IDS=""), \
                mock.patch.object(postbox_auth.imap, "authenticate") as old_imap, \
                mock.patch("apps.mail_engine.factory.get_adapter") as factory:
            factory.return_value.verify_primary_password.return_value = True
            self.assertTrue(mailbox_in_mfa_scope(self.canary))
            self.assertFalse(can_start_mfa_enrollment(self.canary))
            self.assertIsNone(postbox_auth.session_for_token(old_raw))
            self.assertTrue(postbox_auth.trusted_primary_authenticate(
                self.canary.email, "synthetic-primary",
            ))
            with self.assertRaises(postbox_auth.MailboxSecondFactorRequired):
                postbox_auth.sign_in(self.canary.email, "synthetic-primary")
            old_imap.assert_not_called()

    def test_pending_enrollment_challenge_cannot_outlive_canary_scope(self):
        from apps.postbox.two_factor_challenge import create_challenge
        partial = create_challenge(self.canary)
        with override_settings(POSTBOX_2FA_CANARY_MAILBOX_IDS=""):
            response = APIClient().post(
                "/api/postbox/auth/2fa/enroll/",
                {"partial_token": partial, "action": "begin"},
                format="json",
            )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(MailboxTwoFactorSetup.objects.filter(
            mailbox=self.canary,
        ).exists())

    def test_org_required_mailbox_does_not_fall_back_to_public_imap(self):
        self.tenant.two_factor_policy = "require"
        self.tenant.save(update_fields=["two_factor_policy"])
        with override_settings(
            POSTBOX_2FA_CANARY_MAILBOX_IDS="",
            ORGANIZATION_2FA_POLICY_ENABLED=True,
            ORGANIZATION_2FA_ENFORCEMENT_READY=True,
        ), mock.patch.object(postbox_auth.imap, "authenticate") as old_imap, \
                mock.patch("apps.mail_engine.factory.get_adapter") as adapter:
            adapter.return_value.verify_primary_password.return_value = True
            self.assertTrue(postbox_auth.trusted_primary_authenticate(
                self.control.email, "synthetic-password",
            ))
            old_imap.assert_not_called()

    def test_general_availability_preserves_unenrolled_legacy_login(self):
        with override_settings(
            POSTBOX_2FA_CANARY_ENABLED=False,
            POSTBOX_2FA_CANARY_MAILBOX_IDS="",
        ), mock.patch.object(postbox_auth.imap, "authenticate", return_value=True) as old_imap, \
                mock.patch("apps.mail_engine.factory.get_adapter") as adapter:
            self.assertTrue(can_start_mfa_enrollment(self.control))
            self.assertTrue(postbox_auth.trusted_primary_authenticate(
                self.control.email, "synthetic-password",
            ))
            old_imap.assert_called_once_with(self.control.email, "synthetic-password")
            adapter.assert_not_called()

    def test_organization_off_blocks_new_enrollment_but_not_password_login(self):
        with override_settings(
            POSTBOX_2FA_CANARY_ENABLED=False,
            ORGANIZATION_2FA_POLICY_ENABLED=True,
        ), mock.patch.object(postbox_auth.imap, "authenticate", return_value=True) as old_imap, \
                mock.patch("apps.mail_engine.factory.get_adapter") as adapter:
            self.assertFalse(can_start_mfa_enrollment(self.control))
            self.assertFalse(mailbox_in_mfa_scope(self.control))
            self.assertTrue(postbox_auth.trusted_primary_authenticate(
                self.control.email, "synthetic-password",
            ))
            old_imap.assert_called_once()
            adapter.assert_not_called()

            raw, _ = PostBoxSession.issue(self.control)
            client = APIClient()
            client.cookies[postbox_auth.SESSION_COOKIE_NAME] = raw
            with mock.patch.object(postbox_auth, "trusted_primary_authenticate") as verify:
                denied = client.post(
                    "/api/postbox/auth/2fa/start/",
                    {"current_password": "synthetic-password"}, format="json",
                )
            self.assertEqual(denied.status_code, 503)
            verify.assert_not_called()

    def test_organization_enable_allows_voluntary_enrollment_without_client_disruption(self):
        self.tenant.two_factor_policy = "encourage"
        self.tenant.save(update_fields=["two_factor_policy"])
        with override_settings(
            POSTBOX_2FA_CANARY_ENABLED=False,
            ORGANIZATION_2FA_POLICY_ENABLED=True,
        ), mock.patch.object(postbox_auth.imap, "authenticate", return_value=True) as old_imap, \
                mock.patch("apps.mail_engine.factory.get_adapter") as adapter:
            self.assertTrue(can_start_mfa_enrollment(self.control))
            self.assertTrue(postbox_auth.trusted_primary_authenticate(
                self.control.email, "synthetic-password",
            ))
            old_imap.assert_called_once()
            adapter.assert_not_called()

    def test_organization_off_cannot_downgrade_already_enrolled_mfa(self):
        MailboxTwoFactorSetup.objects.create(
            mailbox=self.control,
            enabled_at=timezone.now(),
            totp_secret_encrypted=encrypt_totp_secret(pyotp.random_base32()),
        )
        with override_settings(
            POSTBOX_2FA_CANARY_ENABLED=False,
            ORGANIZATION_2FA_POLICY_ENABLED=True,
        ), mock.patch.object(postbox_auth.imap, "authenticate") as old_imap, \
                mock.patch("apps.mail_engine.factory.get_adapter") as adapter:
            adapter.return_value.verify_primary_password.return_value = True
            self.assertFalse(can_start_mfa_enrollment(self.control))
            self.assertTrue(mailbox_in_mfa_scope(self.control))
            self.assertTrue(postbox_auth.mailbox_requires_two_factor(self.control))
            self.assertTrue(postbox_auth.trusted_primary_authenticate(
                self.control.email, "synthetic-password",
            ))
            old_imap.assert_not_called()
            adapter.return_value.verify_primary_password.assert_called_once()

    def test_general_availability_keeps_tenant_choices_isolated(self):
        owner = make_user("another-ga-owner@example.test")
        other_tenant = make_tenant(owner, "Other GA tenant", "other-ga")
        domain = make_domain(other_tenant, "other-ga.example.test")
        other_mailbox = make_mailbox(other_tenant, domain, "staff")
        self.tenant.two_factor_policy = "encourage"
        self.tenant.save(update_fields=["two_factor_policy"])
        with override_settings(
            POSTBOX_2FA_CANARY_ENABLED=False,
            ORGANIZATION_2FA_POLICY_ENABLED=True,
        ):
            self.assertTrue(can_start_mfa_enrollment(self.control))
            self.assertFalse(can_start_mfa_enrollment(other_mailbox))
            self.assertFalse(mailbox_in_mfa_scope(other_mailbox))

