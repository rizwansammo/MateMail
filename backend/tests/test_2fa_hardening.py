"""
Second-factor brute force and replay.

Phase 0 built a good per-challenge token: opaque, server-side, short-lived,
single-use, password-bound, five attempts. These tests cover the two things
that design does not stop on its own — an attacker who holds the password and
simply asks for a new challenge after every fifth failure, and an attacker who
has observed one valid code inside the ninety seconds it stays valid.

The Phase 0 protections are asserted here too, so a future change cannot trade
one for the other.
"""
from unittest import mock

import pyotp
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.challenge import MAX_ATTEMPTS, peek_challenge
from apps.accounts.models import TwoFactorSetup
from apps.security.limits import TWO_FACTOR_MANAGE_PER_USER, TWO_FACTOR_PER_USER
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
        "LOCATION": "twofactor-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class TwoFactorLoginTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("2fa@example.test")
        self.tenant = make_tenant(self.user, name="TwoFa", slug="two-fa")
        self.secret = enable_2fa(self.user)

    def _challenge(self):
        res = self.client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            REMOTE_ADDR="198.51.100.1",
        )
        self.assertTrue(res.data["requires_2fa"])
        return res.data["partial_token"]

    def _verify(self, token, code):
        return self.client.post(
            "/api/auth/2fa/verify/",
            {"partial_token": token, "code": code},
            REMOTE_ADDR="198.51.100.1",
        )

    # ── Phase 0 behaviour that must survive ────────────────────────────────

    def test_a_valid_code_still_signs_in(self):
        res = self._verify(self._challenge(), totp_now(self.secret))
        self.assertEqual(res.status_code, 200)
        self.assertIn("access", res.data)

    def test_the_challenge_is_still_single_use(self):
        token = self._challenge()
        self._verify(token, totp_now(self.secret))
        again = self._verify(token, totp_now(self.secret))
        self.assertEqual(again.status_code, 401)

    def test_the_challenge_is_still_destroyed_after_five_failures(self):
        token = self._challenge()
        for _ in range(MAX_ATTEMPTS):
            self._verify(token, "000000")
        self.assertIsNone(peek_challenge(token))

    def test_the_challenge_token_is_not_an_access_token(self):
        token = self._challenge()
        res = self.client.get("/api/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertIn(res.status_code, (401, 403))

    # ── What P3b adds ──────────────────────────────────────────────────────

    def test_a_fresh_challenge_does_not_reset_the_attempt_budget(self):
        """
        The bypass the per-challenge counter cannot see.

        An attacker holding the password burns five attempts, requests another
        challenge, and continues — unbounded guesses against six digits. The
        per-user limit follows the account, not the token.
        """
        spent = 0
        while spent < TWO_FACTOR_PER_USER.limit:
            token = self._challenge()
            for _ in range(min(MAX_ATTEMPTS, TWO_FACTOR_PER_USER.limit - spent)):
                self._verify(token, "000000")
                spent += 1

        blocked = self._verify(self._challenge(), "000000")
        self.assertEqual(blocked.status_code, 429)

    def test_the_per_user_lock_also_refuses_a_correct_code(self):
        """A lock that a correct code walks through is not a lock."""
        for _ in range(TWO_FACTOR_PER_USER.limit):
            self._verify(self._challenge(), "000000")
        blocked = self._verify(self._challenge(), totp_now(self.secret))
        self.assertEqual(blocked.status_code, 429)

    def test_a_successful_verification_clears_the_budget(self):
        for _ in range(TWO_FACTOR_PER_USER.limit - 1):
            self._verify(self._challenge(), "000000")
        ok = self._verify(self._challenge(), totp_now(self.secret))
        self.assertEqual(ok.status_code, 200)
        # Fresh budget: this would be the sixth failure without the reset.
        self.assertEqual(self._verify(self._challenge(), "000000").status_code, 401)

    def test_an_accepted_code_cannot_be_replayed(self):
        """
        A TOTP code is valid for its whole timestep — ninety seconds here,
        since pyotp is called with valid_window=1. Anyone who observes one
        (a phishing relay, a shared screen) can present it again inside that
        window against a challenge of their own.
        """
        code = totp_now(self.secret)
        first = self._verify(self._challenge(), code)
        self.assertEqual(first.status_code, 200)

        second = self._verify(self._challenge(), code)
        self.assertEqual(second.status_code, 401)
        self.assertIn("Invalid", second.data["detail"])

    def test_replay_is_scoped_to_the_user(self):
        """Two accounts can legitimately hold the same six digits at once."""
        other = make_user("2fa-other@example.test")
        other_secret = enable_2fa(other)

        code = totp_now(self.secret)
        self._verify(self._challenge(), code)

        with mock.patch.object(pyotp.TOTP, "verify", return_value=True):
            res = self.client.post(
                "/api/auth/login/",
                {"email": other.email, "password": TEST_PASSWORD},
                REMOTE_ADDR="198.51.100.2",
            )
            second = self.client.post(
                "/api/auth/2fa/verify/",
                {"partial_token": res.data["partial_token"], "code": code},
                REMOTE_ADDR="198.51.100.2",
            )
        self.assertEqual(second.status_code, 200)
        self.assertTrue(other_secret)

    def test_a_replayed_code_counts_as_a_failed_attempt(self):
        code = totp_now(self.secret)
        self._verify(self._challenge(), code)
        for _ in range(TWO_FACTOR_PER_USER.limit):
            self._verify(self._challenge(), code)
        blocked = self._verify(self._challenge(), code)
        self.assertEqual(blocked.status_code, 429)

    def test_a_backup_code_is_still_accepted_and_burned(self):
        from apps.accounts.models import TwoFactorBackupCode
        from apps.accounts.tokens import generate_backup_codes, hash_token

        raw = generate_backup_codes(1)[0]
        TwoFactorBackupCode.objects.create(user=self.user, code_hash=hash_token(raw))

        self.assertEqual(self._verify(self._challenge(), raw).status_code, 200)
        self.assertEqual(self._verify(self._challenge(), raw).status_code, 401)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class TwoFactorManagementTest(TestCase):
    """
    Enrolment and disable.

    Both were decorated with an AnonRateThrottle subclass, which returns
    immediately for an authenticated request — so both were unlimited: a signed
    -in session could grind six digits against a known secret, or guess the
    account password to turn the second factor off.
    """

    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("2fa-manage@example.test")
        self.tenant = make_tenant(self.user, name="Manage", slug="manage")
        self.api = auth_client(self.user, self.tenant)

    def test_enrolment_code_guessing_is_capped(self):
        self.api.get("/api/auth/2fa/setup/")
        for _ in range(TWO_FACTOR_MANAGE_PER_USER.limit):
            res = self.api.post("/api/auth/2fa/setup/", {"code": "000000"})
            self.assertEqual(res.status_code, 400)
        self.assertEqual(
            self.api.post("/api/auth/2fa/setup/", {"code": "000000"}).status_code, 429
        )

    def test_a_correct_enrolment_code_still_works(self):
        res = self.api.get("/api/auth/2fa/setup/")
        code = pyotp.TOTP(res.data["secret"]).now()
        confirmed = self.api.post("/api/auth/2fa/setup/", {"code": code})
        self.assertEqual(confirmed.status_code, 200)
        self.assertIn("backup_codes", confirmed.data)

    def test_disable_password_guessing_is_capped(self):
        enable_2fa(self.user)
        for _ in range(TWO_FACTOR_MANAGE_PER_USER.limit):
            res = self.api.post("/api/auth/2fa/disable/", {"password": "nope-not-it"})
            self.assertEqual(res.status_code, 400)
        self.assertEqual(
            self.api.post(
                "/api/auth/2fa/disable/", {"password": "nope-not-it"}
            ).status_code,
            429,
        )

    def test_disable_still_works_with_the_right_password(self):
        enable_2fa(self.user)
        res = self.api.post("/api/auth/2fa/disable/", {"password": TEST_PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.two_factor_enabled)
        self.assertFalse(TwoFactorSetup.objects.filter(user=self.user).exists())


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class ChallengePasswordBindingTest(TestCase):
    """A password reset must void any challenge issued against the old one."""

    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("bound@example.test")
        self.secret = enable_2fa(self.user)

    def test_a_password_change_voids_an_outstanding_challenge(self):
        res = self.client.post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            REMOTE_ADDR="198.51.100.3",
        )
        token = res.data["partial_token"]

        self.user.set_password("A-Completely-New-Passphrase-9")
        self.user.save(update_fields=["password"])

        verified = self.client.post(
            "/api/auth/2fa/verify/",
            {"partial_token": token, "code": totp_now(self.secret)},
            REMOTE_ADDR="198.51.100.3",
        )
        self.assertEqual(verified.status_code, 401)
        self.assertIsNone(peek_challenge(token))
