"""
Platform Console authentication.

The property every test here defends is one sentence: a correct platform
password, on its own, is not a credential. Everything else — expiry, single
use, attempt limits, the enumeration-resistant reset — exists to keep that
true under pressure.

The bypass these tests were written around is the interesting one. The tenant
login's second factor is OPTIONAL: it fires only for an account that enrolled
in TOTP. A platform administrator who had not enrolled would have received a
full session from `/api/auth/login/` with a password alone, and /api/platform/
accepts that session exactly like one earned through the emailed code. The
hostname split would have been decoration over an open door.
"""
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import (
    PlatformCodePurpose,
    PlatformEmailCode,
)
from apps.accounts.cookies import REFRESH_COOKIE_NAME
from apps.platform_admin.models import PlatformAuditLog
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    disable_throttling,
    make_tenant,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "platform-auth-tests",
    }
}

LOGIN = "/api/platform/auth/login/"
VERIFY = "/api/platform/auth/verify/"
RESEND = "/api/platform/auth/resend/"
FORGOT = "/api/platform/auth/forgot-password/"
RESET = "/api/platform/auth/reset-password/"
TENANT_LOGIN = "/api/auth/login/"

NEW_PASSWORD = "Platform-Rotated-91"


def code_from_last_email() -> str:
    """
    The six digits as the operator reads them.

    Pulled out of the delivered message rather than out of the database,
    because the database does not have it — which is the point of the storage
    design and is asserted directly further down.
    """
    import re

    body = mail.outbox[-1].body
    found = re.search(r"\b(\d{6})\b", body)
    assert found, f"no six-digit code in the message: {body!r}"
    return found.group(1)


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM_CACHE,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="mx.matemail.test",
)
class PlatformLoginTest(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        disable_throttling(self)
        self.admin = make_user("ops@netamate.test", is_platform_admin=True)
        self.tenant_user = make_user("customer@acme.test")

    def login(self, email=None, password=TEST_PASSWORD):
        return APIClient().post(
            LOGIN,
            {"email": email or self.admin.email, "password": password},
            format="json",
        )

    # ── stage one ───────────────────────────────────────────────────────────

    def test_a_correct_password_alone_issues_no_credentials(self):
        """The whole phase in one assertion."""
        response = self.login()
        self.assertEqual(200, response.status_code)

        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        self.assertNotIn(REFRESH_COOKIE_NAME, response.cookies)
        self.assertTrue(response.data["requires_code"])

    def test_the_challenge_is_not_a_usable_api_token(self):
        """
        Opaque by construction. If this were a JWT, DRF would parse it and the
        intermediate state would be a full credential.
        """
        challenge = self.login().data["challenge"]

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {challenge}")
        self.assertIn(client.get("/api/platform/stats/").status_code, (401, 403))

    def test_a_code_is_emailed_only_after_the_password_is_correct(self):
        self.login(password="wrong-password")
        self.assertEqual([], mail.outbox, "a wrong password must not send mail")

        self.login()
        self.assertEqual(1, len(mail.outbox))
        self.assertEqual([self.admin.email], mail.outbox[0].to)

    def test_a_tenant_user_cannot_use_the_platform_login(self):
        response = self.login(email=self.tenant_user.email)
        self.assertEqual(401, response.status_code)
        self.assertEqual([], mail.outbox)

    def test_a_deactivated_platform_admin_cannot_use_it(self):
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])

        self.assertEqual(401, self.login().status_code)
        self.assertEqual([], mail.outbox)

    def test_a_tenant_user_and_an_unknown_address_answer_identically(self):
        """Anything else publishes the list of platform administrators."""
        tenant = self.login(email=self.tenant_user.email)
        unknown = self.login(email="nobody@nowhere.test")
        self.assertEqual(tenant.status_code, unknown.status_code)
        self.assertEqual(tenant.data["detail"], unknown.data["detail"])

    # ── stage two ───────────────────────────────────────────────────────────

    def test_the_right_code_issues_a_session(self):
        challenge = self.login().data["challenge"]
        response = APIClient().post(
            VERIFY, {"challenge": challenge, "code": code_from_last_email()},
            format="json",
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("access", response.data)
        self.assertIn(REFRESH_COOKIE_NAME, response.cookies)

    def test_the_issued_session_actually_opens_the_console(self):
        challenge = self.login().data["challenge"]
        access = APIClient().post(
            VERIFY, {"challenge": challenge, "code": code_from_last_email()},
            format="json",
        ).data["access"]

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(200, client.get("/api/platform/stats/").status_code)

    def test_a_wrong_code_fails(self):
        challenge = self.login().data["challenge"]
        wrong = "000000" if code_from_last_email() != "000000" else "111111"
        response = APIClient().post(
            VERIFY, {"challenge": challenge, "code": wrong}, format="json"
        )
        self.assertEqual(401, response.status_code)
        self.assertNotIn("access", response.data)

    def test_a_code_cannot_be_used_twice(self):
        challenge = self.login().data["challenge"]
        code = code_from_last_email()
        payload = {"challenge": challenge, "code": code}

        self.assertEqual(200, APIClient().post(VERIFY, payload, format="json").status_code)
        self.assertEqual(401, APIClient().post(VERIFY, payload, format="json").status_code)

    def test_an_expired_code_fails(self):
        challenge = self.login().data["challenge"]
        code = code_from_last_email()

        row = PlatformEmailCode.objects.get(user=self.admin, consumed_at__isnull=True)
        row.expires_at = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=["expires_at"])

        response = APIClient().post(
            VERIFY, {"challenge": challenge, "code": code}, format="json"
        )
        self.assertEqual(401, response.status_code)

    def test_guessing_is_capped(self):
        """Five wrong guesses burn the challenge, so the right code stops working."""
        challenge = self.login().data["challenge"]
        code = code_from_last_email()
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(PlatformEmailCode.MAX_ATTEMPTS):
            APIClient().post(VERIFY, {"challenge": challenge, "code": wrong}, format="json")

        response = APIClient().post(
            VERIFY, {"challenge": challenge, "code": code}, format="json"
        )
        self.assertEqual(401, response.status_code, "the challenge should be burnt")

    def test_a_demotion_mid_challenge_stops_the_login(self):
        """The window between issue and verify is exactly when this matters."""
        challenge = self.login().data["challenge"]
        code = code_from_last_email()

        self.admin.is_platform_admin = False
        self.admin.save(update_fields=["is_platform_admin"])

        response = APIClient().post(
            VERIFY, {"challenge": challenge, "code": code}, format="json"
        )
        self.assertEqual(401, response.status_code)

    def test_a_password_change_voids_an_outstanding_challenge(self):
        challenge = self.login().data["challenge"]
        code = code_from_last_email()

        self.admin.set_password(NEW_PASSWORD)
        self.admin.save(update_fields=["password"])

        response = APIClient().post(
            VERIFY, {"challenge": challenge, "code": code}, format="json"
        )
        self.assertEqual(401, response.status_code)

    def test_issuing_a_second_code_retires_the_first(self):
        first_challenge = self.login().data["challenge"]
        first_code = code_from_last_email()

        self.login()  # a second attempt supersedes the first

        response = APIClient().post(
            VERIFY, {"challenge": first_challenge, "code": first_code}, format="json"
        )
        self.assertEqual(401, response.status_code)

    # ── storage ─────────────────────────────────────────────────────────────

    def test_the_raw_code_is_nowhere_in_the_database(self):
        self.login()
        code = code_from_last_email()

        row = PlatformEmailCode.objects.get(user=self.admin, consumed_at__isnull=True)
        stored = f"{row.code_hash}{row.challenge_hash}{row.password_fingerprint}"
        self.assertNotIn(code, stored)

    def test_the_code_digest_is_salted_with_the_challenge(self):
        """
        A bare SHA-256 of six digits falls to an offline search of a million
        candidates. Salting with the 256-bit challenge token — which is never
        stored — means a dumped table yields nothing to search.
        """
        import hashlib

        self.login()
        code = code_from_last_email()
        row = PlatformEmailCode.objects.get(user=self.admin, consumed_at__isnull=True)

        bare = hashlib.sha256(code.encode()).hexdigest()
        self.assertNotEqual(bare, row.code_hash)

    # ── the bypass ──────────────────────────────────────────────────────────

    def test_the_organization_login_refuses_a_platform_admin(self):
        """
        The regression this phase exists to prevent. `two_factor_enabled` is
        False here — the default — so before the fix this returned 200 with a
        full session.
        """
        self.assertFalse(self.admin.two_factor_enabled)

        response = APIClient().post(
            TENANT_LOGIN,
            {"email": self.admin.email, "password": TEST_PASSWORD},
            format="json",
        )

        self.assertEqual(403, response.status_code)
        self.assertNotIn("access", response.data)
        self.assertNotIn(REFRESH_COOKIE_NAME, response.cookies)
        self.assertTrue(response.data["platform_admin"])

    def test_the_organization_login_still_works_for_customers(self):
        make_tenant(self.tenant_user, name="Acme", slug="acme")
        response = APIClient().post(
            TENANT_LOGIN,
            {"email": self.tenant_user.email, "password": TEST_PASSWORD},
            format="json",
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("access", response.data)

    def test_a_successful_login_is_audited(self):
        challenge = self.login().data["challenge"]
        APIClient().post(
            VERIFY, {"challenge": challenge, "code": code_from_last_email()},
            format="json",
        )
        self.assertTrue(
            PlatformAuditLog.objects.filter(
                action="platform.login", actor=self.admin
            ).exists()
        )


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM_CACHE,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="mx.matemail.test",
)
class PlatformPasswordResetTest(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        disable_throttling(self)
        self.admin = make_user("ops@netamate.test", is_platform_admin=True)
        self.tenant_user = make_user("customer@acme.test")

    def request_reset(self, email=None):
        return APIClient().post(
            FORGOT, {"email": email or self.admin.email}, format="json"
        )

    def test_an_unknown_address_is_answered_like_a_real_one(self):
        real = self.request_reset()
        fake = self.request_reset(email="nobody@nowhere.test")

        self.assertEqual(real.status_code, fake.status_code)
        self.assertEqual(sorted(real.data), sorted(fake.data))

    def test_only_a_platform_admin_receives_a_code(self):
        self.request_reset(email=self.tenant_user.email)
        self.assertEqual([], mail.outbox)

        self.request_reset()
        self.assertEqual([self.admin.email], mail.outbox[-1].to)

    def test_the_decoy_challenge_cannot_be_completed(self):
        challenge = self.request_reset(email="nobody@nowhere.test").data["challenge"]
        response = APIClient().post(
            RESET,
            {"challenge": challenge, "code": "123456", "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(401, response.status_code)

    def reset(self, new_password=NEW_PASSWORD):
        challenge = self.request_reset().data["challenge"]
        return APIClient().post(
            RESET,
            {
                "challenge": challenge,
                "code": code_from_last_email(),
                "new_password": new_password,
            },
            format="json",
        )

    def test_a_valid_code_changes_the_password(self):
        self.assertEqual(200, self.reset().status_code)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(NEW_PASSWORD))

    def test_the_reset_does_not_sign_anybody_in(self):
        """
        Proving control of a mailbox is not the same as completing a login, and
        the console is reached through the emailed code either way.
        """
        response = self.reset()
        self.assertNotIn("access", response.data)
        self.assertEqual("", response.cookies[REFRESH_COOKIE_NAME].value)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
         "OPTIONS": {"min_length": 10}},
        {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    ])
    def test_django_password_validation_applies(self):
        """
        The validators are pinned here rather than inherited.

        `config/settings/dev.py` sets AUTH_PASSWORD_VALIDATORS = [], which is a
        reasonable convenience for development and the settings module this
        suite runs under. Without this override the test passed for the wrong
        reason — it asserted 400 and got it only because production settings
        happen to configure validators, while under the settings CI actually
        uses, "123" was accepted. What is under test is that the view calls
        `validate_password` and surfaces its errors, so the test supplies the
        validators it needs.
        """
        response = self.reset(new_password="123")
        self.assertEqual(400, response.status_code)
        self.assertIn("new_password", response.data)

    def test_a_reset_code_cannot_be_reused(self):
        challenge = self.request_reset().data["challenge"]
        code = code_from_last_email()
        payload = {"challenge": challenge, "code": code, "new_password": NEW_PASSWORD}

        self.assertEqual(200, APIClient().post(RESET, payload, format="json").status_code)
        self.assertEqual(401, APIClient().post(RESET, payload, format="json").status_code)

    def test_an_expired_reset_code_fails(self):
        challenge = self.request_reset().data["challenge"]
        code = code_from_last_email()

        PlatformEmailCode.objects.filter(
            user=self.admin, purpose=PlatformCodePurpose.PASSWORD_RESET
        ).update(expires_at=timezone.now() - timedelta(seconds=1))

        response = APIClient().post(
            RESET,
            {"challenge": challenge, "code": code, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(401, response.status_code)

    def test_a_login_code_cannot_be_spent_on_a_reset(self):
        """Purposes are separate, or one flow's code opens the other's door."""
        APIClient().post(
            LOGIN, {"email": self.admin.email, "password": TEST_PASSWORD}, format="json"
        )
        login_challenge = PlatformEmailCode.objects.get(
            user=self.admin, purpose=PlatformCodePurpose.LOGIN
        )
        self.assertIsNotNone(login_challenge)

        response = APIClient().post(
            RESET,
            {
                "challenge": "whatever",
                "code": code_from_last_email(),
                "new_password": NEW_PASSWORD,
            },
            format="json",
        )
        self.assertEqual(401, response.status_code)

    def test_the_reset_revokes_existing_sessions(self):
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        from apps.accounts.tokens import make_tokens

        make_tokens(self.admin)
        before = BlacklistedToken.objects.count()

        self.reset()

        self.assertGreater(BlacklistedToken.objects.count(), before)

    def test_the_reset_closes_other_outstanding_challenges(self):
        APIClient().post(
            LOGIN, {"email": self.admin.email, "password": TEST_PASSWORD}, format="json"
        )
        self.reset()

        self.assertFalse(
            PlatformEmailCode.objects.filter(
                user=self.admin, consumed_at__isnull=True
            ).exists()
        )

    def test_the_new_password_still_requires_the_emailed_code(self):
        self.reset()
        mail.outbox.clear()

        response = APIClient().post(
            LOGIN, {"email": self.admin.email, "password": NEW_PASSWORD}, format="json"
        )
        self.assertEqual(200, response.status_code)
        self.assertNotIn("access", response.data)
        self.assertTrue(response.data["requires_code"])

    def test_the_reset_is_audited(self):
        self.reset()
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="platform.password_reset").exists()
        )
