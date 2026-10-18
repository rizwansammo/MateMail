"""Stage 7B: distinct mailbox principal and MFA status are explicit in the UI API."""
from unittest import mock

import pyotp
from django.test import TestCase, override_settings
from django.utils import timezone
from cryptography.fernet import Fernet
from rest_framework.test import APIClient

from apps.postbox import auth as postbox_auth
from apps.postbox.models import PostBoxSession, MailboxTwoFactorSetup
from apps.postbox.two_factor_core import encrypt_totp_secret
from tests.factories import (
    FAST_PASSWORD_HASHERS, make_user, make_tenant, make_domain, make_mailbox,
)

@override_settings(
    CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "phase7b-security-status-tests",
    }},
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    POSTBOX_2FA_ENABLED=True,
    SMTP_IMAP_SECURITY_ENABLED=True,
    MAIL_ENGINE_ADAPTER="native",
    POSTBOX_2FA_FERNET_KEY=Fernet.generate_key().decode(),
)
class MailboxSecurityStatusAPITest(TestCase):
    def setUp(self):
        owner = make_user("stage7b-owner@example.test")
        self.tenant = make_tenant(owner, "Stage 7B", "stage7b-status")
        domain = make_domain(self.tenant, "stage7b.example.test")
        self.mailbox = make_mailbox(self.tenant, domain, "alice")
        self.mailbox.mail_engine_provisioned = True
        self.mailbox.save(update_fields=["mail_engine_provisioned"])
        raw, _session = PostBoxSession.issue(self.mailbox)
        self.client = APIClient()
        self.client.cookies[postbox_auth.SESSION_COOKIE_NAME] = raw

    def _get(self, *, dedicated=False):
        client_adapter = mock.Mock()
        client_adapter.list_client_credentials.return_value = {
            "enabled": True, "dedicated_required": dedicated,
            "credentials": [],
        }
        with mock.patch(
            "apps.postbox.two_factor_views.get_adapter",
            return_value=client_adapter,
        ):
            response = self.client.get("/api/postbox/security/smtp-imap/")
        return response

    def test_native_dedicated_requirement_is_not_mailbox_mfa_enrollment(self):
        response = self._get(dedicated=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["mailbox_address"], self.mailbox.email)
        self.assertTrue(response.data["dedicated_required"])
        self.assertFalse(response.data["two_factor_enabled"])
        self.assertIn(response.data["organization_policy"], ("off", "encourage", "require"))

    def test_disabled_feature_never_presents_credentials(self):
        with override_settings(POSTBOX_2FA_ENABLED=False):
            response = self._get()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("credentials", response.data)

    def test_enrolled_authenticator_is_distinct_from_native_policy(self):
        MailboxTwoFactorSetup.objects.create(
            mailbox=self.mailbox,
            totp_secret_encrypted=encrypt_totp_secret(pyotp.random_base32()),
            enabled_at=timezone.now(),
        )
        # A pre-2FA browser session is deliberately rejected for an enrolled
        # mailbox; create an explicitly MFA-proven session to reach its UI API.
        raw, _session = PostBoxSession.issue(self.mailbox, mfa_verified=True)
        self.client.cookies[postbox_auth.SESSION_COOKIE_NAME] = raw
        response = self._get(dedicated=False)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["two_factor_enabled"])
        self.assertFalse(response.data["dedicated_required"])
