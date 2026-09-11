"""
Regression tests for the 2FA challenge-token bypass.

Before Phase 0, POST /api/auth/login/ returned a real simplejwt AccessToken as
the "partial_token" for a 2FA-enabled account. DRF's JWTAuthentication accepted
it, so knowing only the password granted full API access without ever presenting
the second factor. These tests pin the fix shut.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.challenge import MAX_ATTEMPTS
from apps.accounts.cookies import REFRESH_COOKIE_NAME
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    bearer_client,
    disable_throttling,
    enable_2fa,
    make_tenant,
    make_user,
    totp_now,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "2fa-tests",
    }
}

# Endpoints that must never accept a 2FA challenge token.
PROTECTED_ENDPOINTS = ["/api/auth/me/", "/api/domains/", "/api/mailboxes/", "/api/billing/"]


@override_settings(CACHES=LOCMEM_CACHE, PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class TwoFactorChallengeTest(TestCase):
    def setUp(self):
        cache.clear()
        # Throttling would turn this class's multi-attempt tests into 429s and
        # mask the authorization result being asserted.
        disable_throttling(self)
        self.user = make_user("2fa@example.test", full_name="TwoFa")
        self.tenant = make_tenant(self.user, name="TwoFa Co", slug="twofa-co")
        self.secret = enable_2fa(self.user)

    def _login(self):
        return APIClient().post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
        )

    def _challenge(self):
        res = self._login()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["requires_2fa"])
        return res.data["partial_token"]

    # ── The bypass itself ────────────────────────────────────────────────────

    def test_login_with_2fa_does_not_return_api_tokens(self):
        res = self._login()
        self.assertTrue(res.data.get("requires_2fa"))
        self.assertNotIn("access", res.data)
        self.assertNotIn("refresh", res.data)

    def test_challenge_token_is_not_a_jwt(self):
        """A JWT would be parseable by JWTAuthentication; an opaque token is not."""
        self.assertNotIn(".", self._challenge())

    def test_challenge_token_cannot_authenticate_any_api_endpoint(self):
        challenge = self._challenge()
        for path in PROTECTED_ENDPOINTS:
            with self.subTest(path=path):
                res = bearer_client(challenge).get(path)
                self.assertEqual(
                    res.status_code, 401,
                    f"{path} accepted a 2FA challenge token as a credential",
                )

    def test_challenge_token_cannot_mutate(self):
        challenge = self._challenge()
        res = bearer_client(challenge).post(
            "/api/domains/", {"domain": "bypass.example"}, format="json"
        )
        self.assertEqual(res.status_code, 401)

    # ── The happy path still works ───────────────────────────────────────────

    def test_valid_2fa_returns_usable_tokens(self):
        challenge = self._challenge()
        client = APIClient()
        res = client.post(
            "/api/auth/2fa/verify/",
            {"partial_token": challenge, "code": totp_now(self.secret)},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("access", res.data)
        # Since P3c the refresh token is an HttpOnly cookie, never a body
        # field. Asserting both halves: it arrived, and it did not arrive
        # somewhere a script could read it.
        self.assertNotIn("refresh", res.data)
        self.assertIn(REFRESH_COOKIE_NAME, client.cookies)
        self.assertTrue(client.cookies[REFRESH_COOKIE_NAME]["httponly"])

        # The issued access token must actually work.
        me = bearer_client(res.data["access"]).get("/api/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["email"], self.user.email)

    def test_verified_session_carries_tenant_context(self):
        challenge = self._challenge()
        res = APIClient().post(
            "/api/auth/2fa/verify/",
            {"partial_token": challenge, "code": totp_now(self.secret)},
            format="json",
        )
        self.assertEqual(res.data["tenant"]["id"], str(self.tenant.id))
        listing = bearer_client(res.data["access"]).get("/api/domains/")
        self.assertEqual(listing.status_code, 200)

    # ── Challenge hygiene ────────────────────────────────────────────────────

    def test_challenge_is_single_use(self):
        challenge = self._challenge()
        code = totp_now(self.secret)
        first = APIClient().post(
            "/api/auth/2fa/verify/",
            {"partial_token": challenge, "code": code},
            format="json",
        )
        self.assertEqual(first.status_code, 200)

        replay = APIClient().post(
            "/api/auth/2fa/verify/",
            {"partial_token": challenge, "code": code},
            format="json",
        )
        self.assertEqual(replay.status_code, 401)

    def test_unknown_challenge_is_rejected(self):
        res = APIClient().post(
            "/api/auth/2fa/verify/",
            {"partial_token": "not-a-real-challenge-token", "code": "123456"},
            format="json",
        )
        self.assertEqual(res.status_code, 401)

    def test_challenge_destroyed_after_repeated_wrong_codes(self):
        challenge = self._challenge()
        for _ in range(MAX_ATTEMPTS):
            res = APIClient().post(
                "/api/auth/2fa/verify/",
                {"partial_token": challenge, "code": "000000"},
                format="json",
            )
            self.assertEqual(res.status_code, 401)

        # Even the correct code must now fail: the challenge is gone.
        res = APIClient().post(
            "/api/auth/2fa/verify/",
            {"partial_token": challenge, "code": totp_now(self.secret)},
            format="json",
        )
        self.assertEqual(res.status_code, 401)

    def test_challenge_is_void_after_password_change(self):
        challenge = self._challenge()
        self.user.set_password("A-Different-Passphrase-77")
        self.user.save(update_fields=["password"])

        res = APIClient().post(
            "/api/auth/2fa/verify/",
            {"partial_token": challenge, "code": totp_now(self.secret)},
            format="json",
        )
        self.assertEqual(res.status_code, 401)
