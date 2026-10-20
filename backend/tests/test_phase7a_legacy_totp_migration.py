"""Exercise historical plaintext TOTP migration on disposable test database."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings

from apps.accounts.totp_crypto import decrypt_secret

OTP = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
LEGACY = ("accounts", "0003_platformemailcode")
# The disposable migration test MUST restore the full current schema, not
# only the historical recovery-email migration. Phase 3 added new User fields
# and a tenant-scoped recovery ticket dependent on those fields.
CURRENT = [
    ("accounts", "0006_hub_mfa_recovery_account_guards"),
    ("tenants", "0008_admin_mfa_recovery_ticket"),
]


@override_settings(HUB_2FA_FERNET_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
class ExistingHubAuthenticatorMigrationTest(TransactionTestCase):
    databases = {"default"}

    def test_plaintext_existing_totp_encrypts_without_changing_secret(self):
        executor = MigrationExecutor(connection)
        executor.migrate([LEGACY])
        old_apps = executor.loader.project_state([LEGACY]).apps
        User = old_apps.get_model("accounts", "User")
        Setup = old_apps.get_model("accounts", "TwoFactorSetup")
        user = User.objects.create(
            email="phase7a-existing-otp@example.test",
            password="unusable-test-hash",
            is_active=True, two_factor_enabled=True,
        )
        Setup.objects.create(user_id=user.id, totp_secret=OTP)
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(CURRENT)
            new_apps = executor.loader.project_state(CURRENT).apps
            migrated = new_apps.get_model("accounts", "TwoFactorSetup").objects.get(
                user_id=user.id
            )
            self.assertTrue(migrated.totp_secret.startswith("enc:v1:"))
            self.assertNotIn(OTP, migrated.totp_secret)
            self.assertEqual(decrypt_secret(migrated.totp_secret), OTP)
            executor = MigrationExecutor(connection)
            executor.migrate([LEGACY])
            rolled_back_apps = executor.loader.project_state([LEGACY]).apps
            restored = rolled_back_apps.get_model(
                "accounts", "TwoFactorSetup"
            ).objects.get(user_id=user.id)
            self.assertEqual(restored.totp_secret, OTP)
            executor = MigrationExecutor(connection)
            executor.migrate(CURRENT)
            final_apps = executor.loader.project_state(CURRENT).apps
            reencrypted = final_apps.get_model(
                "accounts", "TwoFactorSetup"
            ).objects.get(user_id=user.id)
            self.assertEqual(decrypt_secret(reencrypted.totp_secret), OTP)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(CURRENT)
