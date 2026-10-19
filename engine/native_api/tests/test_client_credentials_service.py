"""Phase 7A: isolated Native API credential authorization and secret lifecycle."""
import os
import unittest
from unittest import mock

import app
import client_credentials
from validation import ValidationError


class ClientCredentialServiceTest(unittest.TestCase):
    def test_all_mutations_default_denied(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for call in (
                lambda: client_credentials.issue_credential(None, "alice@example.test", "Laptop"),
                lambda: client_credentials.set_policy(None, "alice@example.test", True, True),
                lambda: client_credentials.revoke_credential(None, "alice@example.test", "anything"),
                lambda: client_credentials.list_credentials(None, "alice@example.test"),
            ):
                with self.assertRaises(PermissionError):
                    call()

    def test_primary_verify_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError):
                client_credentials.verify_primary(None, "alice@example.test", "password")

    def test_protected_domain_and_unknown_mailbox_refused(self):
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true"}, clear=True):
            cur = mock.MagicMock()
            cur.fetchone.return_value = None
            conn = mock.MagicMock()
            conn.cursor.return_value.__enter__.return_value = cur
            with self.assertRaises(ValidationError):
                client_credentials.issue_credential(conn, "security@system.matemail.pro", "Bad")
            self.assertIn("platform_reserved_domain", cur.execute.call_args.args[0])

    def test_credential_generation_is_one_time_and_hash_only_on_insert(self):
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true"}, clear=True), \
             mock.patch.object(client_credentials, "_customer_mailbox", return_value=(22, "unused")), \
             mock.patch.object(client_credentials, "hash_password", return_value="{BLF-CRYPT}hash") as bcrypt:
            conn = mock.MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            cur.fetchone.side_effect = [(0,), ("123e4567-e89b-12d3-a456-426614174000",)]
            result = client_credentials.issue_credential(conn, "alice@example.test", "Laptop")
            self.assertEqual(result["id"], "123e4567-e89b-12d3-a456-426614174000")
            self.assertGreaterEqual(len(result["one_time_credential"]), 40)
            bcrypt.assert_called_once_with(result["one_time_credential"])
            stmt, params = cur.execute.call_args.args
            self.assertNotIn(result["one_time_credential"], str(params))
            self.assertEqual(params[-1], "{BLF-CRYPT}hash")

    def test_primary_verifier_uses_strict_bcrypt_hash(self):
        with mock.patch.dict(os.environ, {"NATIVE_TRUSTED_WEB_AUTH_ENABLED": "true"}, clear=True), \
             mock.patch.object(client_credentials, "_customer_mailbox",
                               return_value=(7, "{BLF-CRYPT}stored")), \
             mock.patch.object(client_credentials, "verify_password", return_value=True) as bcrypt:
            result = client_credentials.verify_primary(None, "alice@example.test", "right")
            self.assertEqual(result, {"valid": True})
            bcrypt.assert_called_once_with("right", "{BLF-CRYPT}stored")

    def test_policy_bools_are_not_truthy_strings(self):
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true"}, clear=True), \
             mock.patch.object(client_credentials, "_customer_mailbox", return_value=(22, "unused")):
            for value in ("false", 1, None):
                with self.assertRaises(ValidationError):
                    client_credentials.set_policy(mock.MagicMock(), "alice@example.test", value, False)

    def test_request_payload_rejects_hidden_fields(self):
        with self.assertRaises(ValidationError):
            app._client_issue(None, {"address": "alice@example.test", "label": "Laptop",
                                    "is_platform": True})


if __name__ == "__main__":
    unittest.main()
