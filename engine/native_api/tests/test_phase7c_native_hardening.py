"""Stage 7C-2 hardening: deny timing oracle and credential cap races."""
import os
import unittest
from unittest import mock

import client_credentials
from validation import ValidationError


class NativeCredentialConcurrencyTests(unittest.TestCase):
    def test_missing_mailbox_executes_bcrypt_before_generic_denial(self):
        with mock.patch.dict(os.environ, {
            "NATIVE_TRUSTED_WEB_AUTH_ENABLED": "true",
        }), mock.patch.object(
            client_credentials, "_customer_mailbox",
            side_effect=ValidationError("Mailbox unavailable", "address"),
        ), mock.patch.object(
            client_credentials, "verify_password", return_value=True,
        ) as bcrypt:
            result = client_credentials.verify_primary(
                mock.Mock(), "absent@synthetic.invalid", "wrong-test-password",
            )
        self.assertEqual(result, {"valid": False})
        bcrypt.assert_called_once()
        self.assertEqual(bcrypt.call_args.args[0], "wrong-test-password")
        self.assertTrue(bcrypt.call_args.args[1].startswith("{BLF-CRYPT}"))

    def test_create_locks_mailbox_before_enforcing_active_password_cap(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(10,)]
        with mock.patch.dict(os.environ, {
            "NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true",
        }), mock.patch.object(
            client_credentials, "_customer_mailbox", return_value=(31, "unused"),
        ), mock.patch.object(client_credentials, "hash_password") as hash_fn:
            with self.assertRaises(ValidationError):
                client_credentials.issue_credential(
                    conn, "alice@synthetic.invalid", "Tenth+ Device",
                )
        statements = [call.args[0] for call in cur.execute.call_args_list]
        self.assertIn("FOR UPDATE", statements[0])
        self.assertIn("count(*)", statements[1])
        self.assertEqual(len(statements), 2)
        hash_fn.assert_not_called()

    def test_revoke_locks_mailbox_before_updating_credential(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = ("00000000-0000-4000-8000-000000000001",)
        with mock.patch.dict(os.environ, {
            "NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true",
        }), mock.patch.object(
            client_credentials, "_customer_mailbox", return_value=(31, "unused"),
        ):
            result = client_credentials.revoke_credential(
                conn, "alice@synthetic.invalid",
                "00000000-0000-4000-8000-000000000001",
            )
        self.assertEqual(result, {"revoked": True})
        sql = [call.args[0] for call in cur.execute.call_args_list]
        self.assertIn("FOR UPDATE", sql[0])
        self.assertIn("UPDATE public.mail_client_password", sql[1])
        self.assertEqual(cur.execute.call_args_list[1].args[1][0], 31)

    def test_single_dummy_hash_is_valid_bcrypt_and_never_authorizes(self):
        from passwords import verify_password
        self.assertTrue(client_credentials._UNKNOWN_MAILBOX_HASH.startswith("{BLF-CRYPT}"))
        self.assertTrue(verify_password(
            "matemail-nonloginable-timing-decoy",
            client_credentials._UNKNOWN_MAILBOX_HASH,
        ))
        with mock.patch.dict(os.environ, {
            "NATIVE_TRUSTED_WEB_AUTH_ENABLED": "true",
        }), mock.patch.object(
            client_credentials, "_customer_mailbox",
            side_effect=ValidationError("Unavailable", "address"),
        ):
            # The decoy is only a timing equalizer, never a login secret.
            self.assertEqual(client_credentials.verify_primary(
                mock.Mock(), "absent@synthetic.invalid",
                "matemail-nonloginable-timing-decoy",
            ), {"valid": False})


if __name__ == "__main__":
    unittest.main()
