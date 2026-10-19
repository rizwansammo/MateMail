"""Phase 7C: REAL HTTP Django integration through a disposable LiveServer.

The Django URL stack, rate-limit middleware, Redis cache, PostgreSQL database,
JWT and PostBox cookies are real. Native password-verifier and IMAP folder
provisioning are the ONLY deliberately mocked boundaries: there is no Native
Engine in this test. Never run this suite against production infrastructure.
"""
import json
import urllib.error
import urllib.request
from unittest import mock

import pyotp
from cryptography.fernet import Fernet
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from apps.postbox import auth as postbox_auth
from apps.postbox.models import MailboxTwoFactorSetup, PostBoxSession
from apps.postbox.two_factor_core import encrypt_totp_secret
from tests.factories import (
    FAST_PASSWORD_HASHERS, TEST_PASSWORD, enable_2fa,
    make_domain, make_mailbox, make_tenant, make_user,
)

# A disposable test secret; no production key is read.
TEST_FERNET = Fernet.generate_key().decode("ascii")


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    POSTBOX_2FA_ENABLED=True,
    SMTP_IMAP_SECURITY_ENABLED=True,
    MAIL_ENGINE_ADAPTER="native",
    ORGANIZATION_2FA_POLICY_ENABLED=True,
    ORGANIZATION_2FA_ENFORCEMENT_READY=False,
    HUB_2FA_FERNET_KEY=TEST_FERNET,
    POSTBOX_2FA_FERNET_KEY=TEST_FERNET,
)
class Stage7CRealHTTPBoundaryTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("stage7c-owner@synthetic.example.test")
        self.tenant = make_tenant(
            self.user, "Stage7C Synthetic", "stage7c-synthetic"
        )
        domain = make_domain(self.tenant, "stage7c.synthetic.example.test")
        self.mailbox = make_mailbox(self.tenant, domain, "alice")
        self.mailbox.mail_engine_provisioned = True
        self.mailbox.save(update_fields=["mail_engine_provisioned"])

    def http(self, method, route, body=None, *, cookie="", bearer=""):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        request = urllib.request.Request(
            self.live_server_url + route,
            method=method,
            headers=headers,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
        )
        try:
            with urllib.request.urlopen(request, timeout=9) as response:
                status, fields, raw = (
                    response.status, response.headers, response.read()
                )
        except urllib.error.HTTPError as error:
            status, fields, raw = (
                error.code, error.headers, error.read()
            )
        return status, (json.loads(raw) if raw else {}), fields

    def test_hub_partial_challenge_cannot_access_before_totp(self):
        secret = enable_2fa(self.user)
        status, partial, fields = self.http("POST", "/api/auth/login/", {
            "email": self.user.email, "password": TEST_PASSWORD,
        })
        self.assertEqual(status, 200, partial)
        self.assertTrue(partial["requires_2fa"])
        self.assertNotIn("access", partial)
        self.assertNotIn("matemail_refresh=", str(fields))

        status, protected, _ = self.http(
            "GET", f"/api/workspaces/{self.tenant.pk}/security/2fa/",
            bearer=partial["partial_token"],
        )
        self.assertIn(status, (401, 403), protected)

        code = pyotp.TOTP(secret).now()
        status, verified, fields = self.http(
            "POST", "/api/auth/2fa/verify/", {
                "partial_token": partial["partial_token"], "code": code,
            },
        )
        self.assertEqual(status, 200, verified)
        self.assertIn("access", verified)
        refresh_cookies = fields.get_all("Set-Cookie") or []
        self.assertTrue(any(
            "matemail_refresh=" in value and "HttpOnly" in value
            and "SameSite=Strict" in value and "Path=/api/auth/" in value
            for value in refresh_cookies
        ), "Hub session refresh cookie must be restricted")

        status, policy, _ = self.http(
            "GET", f"/api/workspaces/{self.tenant.pk}/security/2fa/",
            bearer=verified["access"],
        )
        self.assertEqual(status, 200, policy)
        self.assertEqual(policy["policy"], "off")
        self.assertFalse(policy["require_ready"])

        status, replay, _ = self.http(
            "POST", "/api/auth/2fa/verify/", {
                "partial_token": partial["partial_token"], "code": code,
            },
        )
        self.assertEqual(status, 401, replay)

    def test_require_activation_stays_locked_with_flags_on(self):
        status, login, _ = self.http("POST", "/api/auth/login/", {
            "email": self.user.email, "password": TEST_PASSWORD,
        })
        self.assertEqual(status, 200, login)
        self.assertIn("access", login)
        status, result, _ = self.http(
            "PATCH", f"/api/workspaces/{self.tenant.pk}/security/2fa/", {
                "policy": "require",
                "current_password": TEST_PASSWORD,
            },
            bearer=login["access"],
        )
        self.assertEqual(status, 409, result)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.two_factor_policy, "off")

    def test_hub_token_never_authorizes_postbox_mailbox_settings(self):
        status, hub, _ = self.http("POST", "/api/auth/login/", {
            "email": self.user.email, "password": TEST_PASSWORD,
        })
        self.assertEqual(status, 200, hub)
        status, forbidden, _ = self.http(
            "GET", "/api/postbox/security/smtp-imap/",
            bearer=hub["access"],
        )
        self.assertIn(status, (401, 403), forbidden)

    def test_mailbox_wrong_primary_never_issues_partial_challenge(self):
        with mock.patch.object(
            postbox_auth, "trusted_primary_authenticate", return_value=False,
        ):
            status, failure, fields = self.http(
                "POST", "/api/postbox/auth/login/", {
                    "email": self.mailbox.email,
                    "password": "incorrect-synthetic-password",
                },
            )
        self.assertEqual(status, 401, failure)
        self.assertNotIn("partial_token", failure)
        self.assertNotIn("__Host-postbox_session=", str(fields))

    def test_legacy_login_retained_when_both_feature_flags_off(self):
        with override_settings(
            POSTBOX_2FA_ENABLED=False,
            SMTP_IMAP_SECURITY_ENABLED=False,
        ), mock.patch(
            "apps.postbox.imap.authenticate", return_value=True,
        ) as legacy_auth, mock.patch(
            "apps.postbox.imap.open_mailbox",
        ) as isolated_imap:
            isolated_imap.return_value.__enter__.return_value.ensure_standard_folders.return_value = []
            status, login, fields = self.http(
                "POST", "/api/postbox/auth/login/", {
                    "email": self.mailbox.email, "password": TEST_PASSWORD,
                },
            )
        self.assertEqual(status, 200, login)
        self.assertIn("session", login)
        self.assertNotIn("partial_token", login)
        legacy_auth.assert_called_once_with(self.mailbox.email, TEST_PASSWORD)
        self.assertIn("__Host-postbox_session=", str(fields))

    def test_old_postbox_session_is_rejected_after_mailbox_mfa_enrollment(self):
        raw, _session = PostBoxSession.issue(self.mailbox)
        cookie = "__Host-postbox_session=" + raw

        status, before, _ = self.http(
            "GET", "/api/postbox/auth/me/", cookie=cookie,
        )
        self.assertEqual(status, 200, before)

        MailboxTwoFactorSetup.objects.create(
            mailbox=self.mailbox,
            totp_secret_encrypted=encrypt_totp_secret(pyotp.random_base32()),
            enabled_at=timezone.now(),
        )
        status, after, _ = self.http(
            "GET", "/api/postbox/auth/me/", cookie=cookie,
        )
        self.assertIn(status, (401, 403), after)

    def test_postbox_real_cookie_is_issued_only_after_second_factor(self):
        secret = pyotp.random_base32()
        MailboxTwoFactorSetup.objects.create(
            mailbox=self.mailbox,
            totp_secret_encrypted=encrypt_totp_secret(secret),
            enabled_at=timezone.now(),
        )
        with mock.patch.object(
            postbox_auth, "trusted_primary_authenticate",
            side_effect=lambda address, password:
                address == self.mailbox.email and password == TEST_PASSWORD,
        ), mock.patch("apps.postbox.imap.open_mailbox") as isolated_imap:
            isolated_imap.return_value.__enter__.return_value.ensure_standard_folders.return_value = []
            status, challenge, headers = self.http(
                "POST", "/api/postbox/auth/login/", {
                    "email": self.mailbox.email,
                    "password": TEST_PASSWORD,
                },
            )
            self.assertEqual(status, 200, challenge)
            self.assertTrue(challenge["requires_2fa"])
            self.assertNotIn("session", challenge)
            self.assertNotIn("__Host-postbox_session=", str(headers))

            status, unauthenticated, _ = self.http(
                "GET", "/api/postbox/security/smtp-imap/",
            )
            self.assertIn(status, (401, 403), unauthenticated)

            status, result, headers = self.http(
                "POST", "/api/postbox/auth/2fa/verify/", {
                    "partial_token": challenge["partial_token"],
                    "code": pyotp.TOTP(secret).now(),
                },
            )
            self.assertEqual(status, 200, result)
            cookies = headers.get_all("Set-Cookie") or []
            token_cookie = next((
                part.split(";", 1)[0] for part in cookies
                if part.startswith("__Host-postbox_session=")
            ), None)
            self.assertIsNotNone(token_cookie, cookies)
            self.assertTrue(any(
                "Secure" in value and "HttpOnly" in value
                for value in cookies if value.startswith("__Host-postbox_session=")
            ))

            status, me, _ = self.http(
                "GET", "/api/postbox/auth/me/", cookie=token_cookie,
            )
            self.assertEqual(status, 200, me)
            self.assertEqual(me["authenticated_mailbox"]["email"], self.mailbox.email)

            status, reused, _ = self.http(
                "POST", "/api/postbox/auth/2fa/verify/", {
                    "partial_token": challenge["partial_token"],
                    "code": pyotp.TOTP(secret).now(),
                },
            )
            self.assertEqual(status, 401, reused)
