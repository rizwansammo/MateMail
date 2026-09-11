"""
Password hashing.

Until now MateMail used Django's default PBKDF2 while `docs/SECURITY.md`
claimed Argon2 — a documentation defect found during P3b and corrected there
rather than papered over. This makes the claim true.

The interesting half is not "new passwords are Argon2". It is that **existing
accounts keep working and are upgraded in place**. A password hash cannot be
converted without the password, so the only safe migration is the one Django
already performs: on a successful login, if the stored hash does not use the
preferred hasher, re-hash the plaintext the user just supplied. Dropping
PBKDF2 from the list would not migrate those accounts, it would lock them out.

These tests deliberately do NOT use the fast test hasher — they are about which
hasher is in force, so overriding it would make them assert nothing.
"""
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.test import TestCase, override_settings

from apps.accounts.models import User
from tests.factories import TEST_PASSWORD

PBKDF2_ONLY = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]


class ConfigurationTest(TestCase):
    def test_argon2_is_the_preferred_hasher(self):
        from django.conf import settings

        self.assertEqual(
            settings.PASSWORD_HASHERS[0],
            "django.contrib.auth.hashers.Argon2PasswordHasher",
        )

    def test_pbkdf2_is_retained_for_existing_accounts(self):
        """
        Not legacy clutter. Without it, every account created before this
        change would stop being able to log in.
        """
        from django.conf import settings

        self.assertIn(
            "django.contrib.auth.hashers.PBKDF2PasswordHasher",
            settings.PASSWORD_HASHERS,
        )

    def test_the_binding_is_installed(self):
        """Django ships the hasher; argon2-cffi is an optional extra."""
        import argon2  # noqa: F401

        from django.contrib.auth.hashers import Argon2PasswordHasher

        self.assertTrue(Argon2PasswordHasher().encode("x", "saltsaltsalt"))

    def test_argon2_is_declared_as_a_dependency(self):
        import pathlib

        requirements = (
            pathlib.Path(__file__).resolve().parent.parent / "requirements.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("argon2-cffi", requirements)


class NewPasswordTest(TestCase):
    def test_a_new_user_gets_an_argon2_hash(self):
        user = User.objects.create_user(
            email="new-argon2@example.test", password=TEST_PASSWORD
        )
        self.assertTrue(user.password.startswith("argon2$"))
        self.assertEqual(identify_hasher(user.password).algorithm, "argon2")

    def test_set_password_uses_argon2(self):
        user = User.objects.create_user(
            email="set-argon2@example.test", password=TEST_PASSWORD
        )
        user.set_password("A-Different-Passphrase-77")
        self.assertEqual(identify_hasher(user.password).algorithm, "argon2")

    def test_the_plaintext_is_not_recoverable_from_the_hash(self):
        user = User.objects.create_user(
            email="opaque@example.test", password=TEST_PASSWORD
        )
        self.assertNotIn(TEST_PASSWORD, user.password)

    def test_the_same_password_hashes_differently_each_time(self):
        a = make_password(TEST_PASSWORD)
        b = make_password(TEST_PASSWORD)
        self.assertNotEqual(a, b)
        self.assertTrue(check_password(TEST_PASSWORD, a))
        self.assertTrue(check_password(TEST_PASSWORD, b))


class LegacyHashTest(TestCase):
    """
    An account created before this change.

    The PBKDF2 hash is produced the way the old code would have produced it,
    then stored with `update()` so nothing re-hashes it on the way in.
    """

    def setUp(self):
        with override_settings(PASSWORD_HASHERS=PBKDF2_ONLY):
            self.legacy_hash = make_password(TEST_PASSWORD)
        self.assertTrue(self.legacy_hash.startswith("pbkdf2_"))

        self.user = User.objects.create_user(
            email="legacy@example.test", password="placeholder-overwritten-below"
        )
        User.objects.filter(pk=self.user.pk).update(password=self.legacy_hash)
        self.user.refresh_from_db()

    def test_the_fixture_really_is_a_legacy_hash(self):
        self.assertEqual(identify_hasher(self.user.password).algorithm, "pbkdf2_sha256")

    def test_a_legacy_hash_still_authenticates(self):
        user = authenticate(username=self.user.email, password=TEST_PASSWORD)
        self.assertIsNotNone(user, "an existing PBKDF2 account can no longer log in")
        self.assertEqual(user.pk, self.user.pk)

    def test_check_password_still_accepts_it(self):
        self.assertTrue(self.user.check_password(TEST_PASSWORD))

    def test_a_successful_login_upgrades_the_hash_to_argon2(self):
        """
        Django's built-in upgrade path: it has the plaintext at exactly this
        moment and never again, so this is the only point at which an existing
        account can be migrated.
        """
        authenticate(username=self.user.email, password=TEST_PASSWORD)

        self.user.refresh_from_db()
        self.assertEqual(
            identify_hasher(self.user.password).algorithm,
            "argon2",
            "a successful login did not upgrade the stored hash",
        )
        self.assertNotEqual(self.user.password, self.legacy_hash)

    def test_the_upgraded_hash_still_verifies(self):
        authenticate(username=self.user.email, password=TEST_PASSWORD)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(TEST_PASSWORD))

    def test_the_upgrade_survives_a_second_login(self):
        authenticate(username=self.user.email, password=TEST_PASSWORD)
        again = authenticate(username=self.user.email, password=TEST_PASSWORD)
        self.assertIsNotNone(again)

    def test_a_wrong_password_still_fails(self):
        self.assertIsNone(
            authenticate(username=self.user.email, password="not-the-password")
        )

    def test_a_wrong_password_does_not_upgrade_the_hash(self):
        """An upgrade on a failed attempt would mean the check was skipped."""
        authenticate(username=self.user.email, password="not-the-password")
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, self.legacy_hash)

    def test_a_wrong_password_fails_against_an_upgraded_hash_too(self):
        authenticate(username=self.user.email, password=TEST_PASSWORD)
        self.assertIsNone(
            authenticate(username=self.user.email, password="still-not-it")
        )


class LoginEndpointTest(TestCase):
    """The upgrade must happen through the real sign-in path, not only ORM calls."""

    def setUp(self):
        with override_settings(PASSWORD_HASHERS=PBKDF2_ONLY):
            legacy = make_password(TEST_PASSWORD)
        self.user = User.objects.create_user(
            email="legacy-api@example.test", password="placeholder"
        )
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])
        User.objects.filter(pk=self.user.pk).update(password=legacy)

    def test_signing_in_through_the_api_upgrades_the_hash(self):
        from rest_framework.test import APIClient

        res = APIClient().post(
            "/api/auth/login/",
            {"email": self.user.email, "password": TEST_PASSWORD},
            format="json",
            REMOTE_ADDR="198.51.100.200",
        )
        self.assertEqual(res.status_code, 200)

        self.user.refresh_from_db()
        self.assertEqual(identify_hasher(self.user.password).algorithm, "argon2")
