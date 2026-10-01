"""
Refresh token storage.

The refresh token used to come back in the JSON body and live in
`localStorage`. It is the durable credential — seven days, and it mints access
tokens for all of them — so any script on the page could take a week of silent
access from a single XSS.

These tests assert both halves: the token arrives where it should, and it does
not arrive anywhere it shouldn't.
"""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.cookies import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
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
        "LOCATION": "refresh-cookie-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class CookieShapeTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.user = make_user("cookie@example.test")
        self.tenant = make_tenant(self.user, name="Cookie", slug="cookie")
        self.client_api = APIClient()

    def _login(self):
        return self.client_api.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
        )

    def test_login_sets_the_cookie(self):
        res = self._login()
        self.assertEqual(res.status_code, 200)
        self.assertIn(REFRESH_COOKIE_NAME, res.cookies)
        self.assertTrue(res.cookies[REFRESH_COOKIE_NAME].value)

    def test_the_cookie_is_httponly(self):
        """The whole point: document.cookie cannot see it."""
        cookie = self._login().cookies[REFRESH_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])

    def test_the_cookie_is_samesite_strict(self):
        cookie = self._login().cookies[REFRESH_COOKIE_NAME]
        self.assertEqual(cookie["samesite"], "Strict")

    def test_the_cookie_is_scoped_to_the_auth_endpoints(self):
        """Every other API call carries no browser-attached credential."""
        cookie = self._login().cookies[REFRESH_COOKIE_NAME]
        self.assertEqual(cookie["path"], REFRESH_COOKIE_PATH)

    def test_the_cookie_expires_with_the_token(self):
        from django.conf import settings

        cookie = self._login().cookies[REFRESH_COOKIE_NAME]
        expected = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
        self.assertEqual(int(cookie["max-age"]), expected)

    @override_settings(REFRESH_COOKIE_SECURE=True)
    def test_the_cookie_is_secure_when_configured(self):
        self.assertTrue(self._login().cookies[REFRESH_COOKIE_NAME]["secure"])

    def test_production_settings_mark_the_cookie_secure(self):
        """A deployment must not be able to ship this over plain HTTP."""
        import config.settings.prod as prod

        self.assertIs(prod.REFRESH_COOKIE_SECURE, True)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class BodyNeverCarriesTheTokenTest(TestCase):
    """
    Every path that mints a token pair.

    A single endpoint still returning `refresh` in the body would undo the
    change for anyone who happens to use it.
    """

    def setUp(self):
        disable_throttling(self)
        self.user = make_user("nobody@example.test")
        self.tenant = make_tenant(self.user, name="NoBody", slug="no-body")

    def _assert_clean(self, response):
        self.assertNotIn("refresh", response.data)
        self.assertNotIn("refresh", str(response.data))

    def test_login(self):
        res = APIClient().post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
        )
        self._assert_clean(res)

    def test_signup(self):
        res = APIClient().post(
            "/api/auth/signup/",
            {
                "email": "fresh@example.test",
                "password": TEST_PASSWORD,
                "full_name": "Fresh",
                "workspace_name": "Fresh Co",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self._assert_clean(res)

    def test_two_factor_verify(self):
        secret = enable_2fa(self.user)
        client = APIClient()
        challenge = client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
        ).data["partial_token"]
        res = client.post(
            "/api/auth/2fa/verify/",
            {"partial_token": challenge, "code": totp_now(secret)},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self._assert_clean(res)

    def test_refresh(self):
        client = APIClient()
        client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
        )
        res = client.post("/api/auth/refresh/")
        self.assertEqual(res.status_code, 200)
        self._assert_clean(res)

    def test_workspace_switch_is_not_an_auth_token_minting_path(self):
        res = auth_client(self.user, self.tenant).post(
            "/api/workspaces/switch/", {"tenant_id": str(self.tenant.id)}, format="json"
        )
        self.assertEqual(res.status_code, 404)

    def test_additional_workspace_creation_is_not_an_auth_token_minting_path(self):
        res = auth_client(self.user, self.tenant).post(
            "/api/workspaces/create/", {"name": "Second"}, format="json"
        )
        self.assertEqual(res.status_code, 404)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class RefreshFlowTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.user = make_user("flow@example.test")
        self.tenant = make_tenant(self.user, name="Flow", slug="flow")
        self.client_api = APIClient()

    def _login(self):
        return self.client_api.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
        )

    def test_refresh_works_from_the_cookie_alone(self):
        self._login()
        res = self.client_api.post("/api/auth/refresh/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("access", res.data)

    def test_the_new_access_token_is_usable(self):
        self._login()
        access = self.client_api.post("/api/auth/refresh/").data["access"]
        me = APIClient()
        me.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(me.get("/api/auth/me/").status_code, 200)

    def test_refresh_rotates_the_cookie(self):
        self._login()
        first = self.client_api.cookies[REFRESH_COOKIE_NAME].value
        res = self.client_api.post("/api/auth/refresh/")
        self.assertNotEqual(res.cookies[REFRESH_COOKIE_NAME].value, first)

    def test_a_rotated_token_cannot_be_reused(self):
        """Rotation without blacklisting would leave a stolen token valid."""
        self._login()
        stolen = self.client_api.cookies[REFRESH_COOKIE_NAME].value
        self.client_api.post("/api/auth/refresh/")

        replay = APIClient()
        replay.cookies[REFRESH_COOKIE_NAME] = stolen
        self.assertEqual(replay.post("/api/auth/refresh/").status_code, 401)

    def test_refresh_without_a_cookie_is_401(self):
        self.assertEqual(APIClient().post("/api/auth/refresh/").status_code, 401)

    def test_a_body_supplied_token_is_not_accepted(self):
        """
        No second mechanism. Accepting the token from the body would let a
        script keep holding one, which is the exposure being removed.
        """
        self._login()
        token = self.client_api.cookies[REFRESH_COOKIE_NAME].value

        body_only = APIClient()
        res = body_only.post("/api/auth/refresh/", {"refresh": token}, format="json")
        self.assertEqual(res.status_code, 401)

    def test_a_garbage_cookie_is_401_and_is_cleared(self):
        client = APIClient()
        client.cookies[REFRESH_COOKIE_NAME] = "not-a-jwt"
        res = client.post("/api/auth/refresh/")
        self.assertEqual(res.status_code, 401)
        # Cleared, so the browser stops presenting a credential the server has
        # already rejected.
        self.assertEqual(res.cookies[REFRESH_COOKIE_NAME].value, "")

    def test_the_refreshed_token_keeps_tenant_context(self):
        self._login()
        access = self.client_api.post("/api/auth/refresh/").data["access"]
        import jwt

        claims = jwt.decode(access, options={"verify_signature": False})
        self.assertEqual(claims["tenant_id"], str(self.tenant.id))

    def test_removed_workspace_switch_cannot_change_refresh_context(self):
        self._login()
        before = self.client_api.cookies[REFRESH_COOKIE_NAME].value
        response = self.client_api.post(
            "/api/workspaces/switch/",
            {"tenant_id": str(self.tenant.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client_api.cookies[REFRESH_COOKIE_NAME].value, before)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class LogoutAndResetTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.user = make_user("bye@example.test")
        self.tenant = make_tenant(self.user, name="Bye", slug="bye")
        self.client_api = APIClient()
        self.access = self.client_api.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
        ).data["access"]
        self.client_api.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")

    def test_logout_clears_the_cookie(self):
        res = self.client_api.post("/api/auth/logout/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.cookies[REFRESH_COOKIE_NAME].value, "")

    def test_logout_clears_it_at_the_matching_path(self):
        """
        A delete_cookie with the wrong path leaves the original in place and
        the user stays refreshable after logging out.
        """
        res = self.client_api.post("/api/auth/logout/")
        self.assertEqual(res.cookies[REFRESH_COOKIE_NAME]["path"], REFRESH_COOKIE_PATH)

    def test_logout_makes_the_token_unusable(self):
        self.client_api.post("/api/auth/logout/")
        self.assertEqual(self.client_api.post("/api/auth/refresh/").status_code, 401)

    def test_logout_needs_no_request_body(self):
        res = self.client_api.post("/api/auth/logout/")
        self.assertEqual(res.status_code, 200)

    def test_password_reset_clears_the_cookie(self):
        from apps.accounts.models import PasswordResetToken

        raw, _ = PasswordResetToken.make(self.user)
        res = APIClient().post(
            "/api/auth/reset-password/",
            {"token": raw, "new_password": "Rotated-Passphrase-88"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.cookies[REFRESH_COOKIE_NAME].value, "")
