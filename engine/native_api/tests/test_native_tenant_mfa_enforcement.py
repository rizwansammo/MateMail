"""Synthetic Native tenant MFA enforcement tests. No production resources."""
import os
import pathlib
import unittest
import uuid
from unittest import mock

import client_credentials
import app
from validation import ValidationError


TENANT = str(uuid.uuid4())
OTHER_TENANT = str(uuid.uuid4())


class TenantNativeMfaTest(unittest.TestCase):
    def _conn(self, found, bound, addresses, *, updated=1):
        cursor = mock.MagicMock()
        cursor.fetchall.side_effect = [found, bound, addresses]
        cursor.rowcount = updated
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        return conn, cursor

    def test_native_tenant_policy_requires_mutation_gate(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PermissionError):
                client_credentials.set_tenant_required(None, TENANT, [], True)

    def test_native_tenant_policy_validates_uuid_and_bools_before_db(self):
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true"}, clear=True):
            for bad in (None, "not-a-uuid", "", OTHER_TENANT + "/x"):
                with self.assertRaises(ValidationError):
                    client_credentials.set_tenant_required(None, bad, [], True)
            for bad in ("true", 1, None):
                with self.assertRaises(ValidationError):
                    client_credentials.set_tenant_required(None, TENANT, [], bad)
            with self.assertRaises(ValidationError):
                client_credentials.set_tenant_required(None, TENANT, ["acme.test", "acme.test"], True)

    def test_required_transition_binds_only_verified_tenant_domain_and_queues_mailboxes(self):
        conn, cursor = self._conn(
            [("acme.test", None, False)], [],
            [("alice@acme.test",), ("bob@acme.test",)],
        )
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true"}, clear=True):
            result = client_credentials.set_tenant_required(conn, TENANT, ["acme.test"], True)
        self.assertEqual(result["required"], True)
        self.assertEqual(result["mailbox_count"], 2)
        self.assertEqual(result["_kick_addresses"], ["alice@acme.test", "bob@acme.test"])
        sql_calls = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("native_tenant_mfa_policy" in statement for statement in sql_calls))
        self.assertTrue(any("SET tenant_id=" in statement for statement in sql_calls))

    def test_cross_tenant_domain_rebinding_fails_before_enforcement(self):
        conn, cursor = self._conn([("acme.test", OTHER_TENANT, False)], [], [])
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true"}, clear=True):
            with self.assertRaises(ValidationError):
                client_credentials.set_tenant_required(conn, TENANT, ["acme.test"], True)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(any("native_tenant_mfa_policy" in statement for statement in statements))

    def test_protected_platform_domain_cannot_be_claimed(self):
        conn, cursor = self._conn([("system.matemail.pro", None, True)], [], [])
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true"}, clear=True):
            with self.assertRaises(ValidationError):
                client_credentials.set_tenant_required(
                    conn, TENANT, ["system.matemail.pro"], True,
                )
        self.assertFalse(any("native_tenant_mfa_policy" in call.args[0]
                             for call in cursor.execute.call_args_list))

    def test_partial_domain_list_and_missing_domain_refused(self):
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true"}, clear=True):
            conn, _ = self._conn([], [], [])
            with self.assertRaises(ValidationError):
                client_credentials.set_tenant_required(conn, TENANT, ["missing.test"], True)
            conn, _ = self._conn([], [("existing.test",)], [])
            with self.assertRaises(ValidationError):
                client_credentials.set_tenant_required(conn, TENANT, [], True)

    def test_empty_tenant_can_be_required_before_first_domain(self):
        conn, cur = self._conn([], [], [])
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_CREDENTIAL_API_ENABLED": "true"}, clear=True):
            result = client_credentials.set_tenant_required(conn, TENANT, [], True)
        self.assertEqual(result["mailbox_count"], 0)
        self.assertEqual(result["_kick_addresses"], [])

    def test_native_http_contract_for_tenant_required_is_strict(self):
        payload = {"tenant_id": TENANT, "domains": ["acme.test"], "required": True}
        with mock.patch.object(client_credentials, "set_tenant_required",
                               return_value={"required": True, "mailbox_count": 0, "_kick_addresses": []}) as setter:
            self.assertEqual(app._tenant_mfa_require(None, payload)["required"], True)
        setter.assert_called_once_with(None, TENANT, ["acme.test"], True)
        with self.assertRaises(ValidationError):
            app._tenant_mfa_require(None, {**payload, "override_otp": True})

    def test_sql_dovecot_gate_is_native_and_applies_before_primary_password(self):
        migration = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "016_native_tenant_mfa_enforcement.sql"
        sql = migration.read_text()
        self.assertIn("tenant_requires AND NOT dedicated", sql)
        self.assertIn("native_tenant_mfa_policy t ON t.tenant_id = d.tenant_id", sql)
        self.assertLess(sql.index("IF tenant_requires AND NOT dedicated THEN"),
                        sql.index("IF NOT dedicated AND public.mail_client_verify_bcrypt"))
        self.assertIn("tenant_id uuid", sql)
        self.assertIn("required boolean NOT NULL DEFAULT false", sql)


if __name__ == "__main__":
    unittest.main()
