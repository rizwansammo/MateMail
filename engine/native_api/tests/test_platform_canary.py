"""Canary activation cannot reveal credentials or mutate customer accounts."""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import platform_canary
from validation import ValidationError

BASE = {
    "NATIVE_API_SECRET": "ordinary-engine-secret-11111111111111111",
    "NATIVE_DOVECOT_POLICY_SECRET": "distinct-dovecot-secret-22222222222222",
    "NATIVE_DB_PASSWORD": "db-secret-for-tests-333333333333333333",
    "NATIVE_PLATFORM_SECRET": "platform-http-secret-44444444444444",
    "NATIVE_PLATFORM_DB_PASSWORD": "platform-db-secret-555555555555555",
    "NATIVE_PLATFORM_OPERATIONS_ENABLED": "True",
    "NATIVE_SYSTEM_CANARY_ACTIVATION_ENABLED": "True",
    "NATIVE_SYSTEM_CANARY_ROLLBACK_ENABLED": "True",
}


class CanaryOnlyTests(unittest.TestCase):
    def test_disabled_gate_does_not_read_secret_or_connect(self):
        env = {**BASE, "NATIVE_SYSTEM_CANARY_ACTIVATION_ENABLED": "False"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(platform_canary, "_read_canary_secret") as secret:
                with mock.patch.object(platform_canary.db, "connect_platform") as db:
                    with self.assertRaises(PermissionError):
                        platform_canary.activate_operations_canary(platform_canary.ACTIVATE_BODY)
                    secret.assert_not_called()
                    db.assert_not_called()

    def test_arbitrary_payloads_fail_before_auth_use(self):
        with mock.patch.dict(os.environ, BASE, clear=True):
            with mock.patch.object(platform_canary, "_read_canary_secret") as secret:
                for body in (
                    {}, [], None, {"domain": "mail.matemail.pro",
                                   "operation": "activate_operations_canary"},
                    {**platform_canary.ACTIVATE_BODY, "password": "wrong"},
                    {**platform_canary.ACTIVATE_BODY, "role": "noreply"},
                ):
                    with self.subTest(body=body), self.assertRaises(ValidationError):
                        platform_canary.activate_operations_canary(body)
                secret.assert_not_called()

    def test_missing_signing_key_fails_before_secret_access(self):
        with mock.patch.dict(os.environ, BASE, clear=True):
            with mock.patch.object(platform_canary, "_check_signer",
                                   side_effect=PermissionError("signing mismatch")):
                with mock.patch.object(platform_canary, "_read_canary_secret") as secret:
                    with self.assertRaises(PermissionError):
                        platform_canary.activate_operations_canary(platform_canary.ACTIVATE_BODY)
                    secret.assert_not_called()

    def test_canary_never_returns_a_hash_or_plaintext(self):
        with mock.patch.dict(os.environ, BASE, clear=True):
            with mock.patch.object(platform_canary, "_check_signer"):
                with mock.patch.object(platform_canary, "_read_canary_secret",
                                       return_value="GENERATED_NON_PUBLIC_CANARY_SECRET_0123456789012345"):
                    with mock.patch.object(platform_canary.passwords, "hash_password",
                                           return_value="{BLF-CRYPT}$2b$10$hashed") as hashpw:
                        with mock.patch.object(platform_canary.db, "connect_platform") as conn:
                            cur = conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
                            cur.fetchone.return_value = ({"active_mailboxes": 1, "sender_switched": False},)
                            result = platform_canary.activate_operations_canary(platform_canary.ACTIVATE_BODY)
            self.assertEqual(result, {"active_mailboxes": 1, "sender_switched": False})
            hashpw.assert_called_once()
            self.assertEqual(cur.execute.call_args.args[0],
                             "SELECT public.platform_activate_system_canary(%s)")
            self.assertNotIn("GENERATED_NON_PUBLIC_CANARY", str(result))

    def test_rollback_requires_independent_gate(self):
        env = {**BASE, "NATIVE_SYSTEM_CANARY_ROLLBACK_ENABLED": "False"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(platform_canary.db, "connect_platform") as conn:
                with self.assertRaises(PermissionError):
                    platform_canary.deactivate_operations_canary(platform_canary.ROLLBACK_BODY)
                conn.assert_not_called()

    def test_secret_must_be_regular_private_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "canary-secret"
            path.write_text("a" * 60)
            path.chmod(0o644)
            with mock.patch.object(platform_canary, "SECRET_PATH", path):
                with self.assertRaises(PermissionError):
                    platform_canary._read_canary_secret()
                path.chmod(0o600)
                self.assertEqual(platform_canary._read_canary_secret(), "a" * 60)
