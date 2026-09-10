"""
Auth security regressions: password-reset session invalidation and
inactive-user JWT handling.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from apps.accounts.models import PasswordResetToken
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    auth_client,
    bearer_client,
    disable_throttling,
    make_tenant,
    make_user,
)

NEW_PASSWORD = "Rotated-Passphrase-88"

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "auth-security-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PasswordResetSecurityTest(TestCase):
    """
    Before Phase 0, ResetPasswordView set the new password and nothing else. A
    stolen refresh token stayed valid for its full 7-day life *after* the victim
    reset their password — the exact action taken on suspecting compromise.
    """

    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("reset@example.test", full_name="Reset")
        self.tenant = make_tenant(self.user, name="Reset Co", slug="reset-co")

    def _reset(self):
        raw, _ = PasswordResetToken.make(self.user)
        return APIClient().post(
            "/api/auth/reset-password/",
            {"token": raw, "new_password": NEW_PASSWORD},
            format="json",
        )

    def test_reset_succeeds_and_new_password_works(self):
        res = self._reset()
        self.assertEqual(res.status_code, 200)
        login = APIClient().post(
            "/api/auth/login/",
            {"email": self.user.email, "password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertIn("access", login.data)

    def test_reset_revokes_outstanding_refresh_tokens(self):
        # Establish a session, then reset the password.
        login = APIClient().post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
        )
        refresh = login.data["refresh"]

        # Sanity: the refresh token works before the reset.
        pre = APIClient().post("/api/auth/refresh/", {"refresh": refresh}, format="json")
        self.assertEqual(pre.status_code, 200)

        self.assertEqual(self._reset().status_code, 200)

        post = APIClient().post("/api/auth/refresh/", {"refresh": refresh}, format="json")
        self.assertEqual(
            post.status_code, 401,
            "a refresh token issued before the password reset is still usable",
        )

    def test_reset_blacklists_every_outstanding_token_for_the_user(self):
        for _ in range(3):
            APIClient().post(
                "/api/auth/login/",
                {"email": self.user.email, "password": TEST_PASSWORD},
                format="json",
            )
        outstanding = OutstandingToken.objects.filter(user=self.user)
        self.assertGreaterEqual(outstanding.count(), 3)

        self.assertEqual(self._reset().status_code, 200)

        for token in outstanding:
            self.assertTrue(
                BlacklistedToken.objects.filter(token=token).exists(),
                "an outstanding refresh token survived the password reset",
            )

    def test_reset_invalidates_other_outstanding_reset_tokens(self):
        stale_raw, stale = PasswordResetToken.make(self.user)
        self.assertEqual(self._reset().status_code, 200)

        stale.refresh_from_db()
        self.assertTrue(stale.is_used, "a second reset link is still redeemable")

        replay = APIClient().post(
            "/api/auth/reset-password/",
            {"token": stale_raw, "new_password": "Third-Passphrase-99"},
            format="json",
        )
        self.assertEqual(replay.status_code, 400)

    def test_used_reset_token_cannot_be_replayed(self):
        raw, _ = PasswordResetToken.make(self.user)
        first = APIClient().post(
            "/api/auth/reset-password/",
            {"token": raw, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(first.status_code, 200)
        second = APIClient().post(
            "/api/auth/reset-password/",
            {"token": raw, "new_password": "Yet-Another-Pass-11"},
            format="json",
        )
        self.assertEqual(second.status_code, 400)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class InactiveUserAuthTest(TestCase):
    """
    Before Phase 0, TenantMiddleware caught only (InvalidToken, TokenError).
    simplejwt raises AuthenticationFailed for a valid token whose user is
    inactive, and middleware runs outside DRF's exception handling — so every
    request from a deactivated user returned an unhandled 500.
    """

    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("deactivate@example.test", full_name="Gone")
        self.tenant = make_tenant(self.user, name="Gone Co", slug="gone-co")
        self.client_with_token = auth_client(self.user, self.tenant)

    def _deactivate(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

    def test_token_works_while_user_is_active(self):
        self.assertEqual(self.client_with_token.get("/api/auth/me/").status_code, 200)

    def test_deactivated_user_gets_401_not_500(self):
        self._deactivate()
        for path in ["/api/auth/me/", "/api/domains/", "/api/mailboxes/", "/api/billing/"]:
            with self.subTest(path=path):
                res = self.client_with_token.get(path)
                self.assertEqual(
                    res.status_code, 401,
                    f"{path} returned {res.status_code} for an inactive user",
                )

    def test_deactivated_user_mutation_is_401_not_500(self):
        self._deactivate()
        res = self.client_with_token.post(
            "/api/domains/", {"domain": "inactive.example"}, format="json"
        )
        self.assertEqual(res.status_code, 401)

    def test_deactivated_user_cannot_log_in(self):
        self._deactivate()
        res = APIClient().post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
        )
        self.assertEqual(res.status_code, 401)

    def test_garbage_bearer_token_is_401_not_500(self):
        res = bearer_client("not.a.jwt").get("/api/auth/me/")
        self.assertEqual(res.status_code, 401)

    def test_unknown_api_key_is_401_not_500(self):
        res = bearer_client("mm_completely-unknown-key-value").get("/api/domains/")
        self.assertEqual(res.status_code, 401)
