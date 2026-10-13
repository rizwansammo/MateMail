"""Release 3 DKIM gate must not accept regular engine credentials or arbitrary input."""
import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import platform_security
from validation import ValidationError

BASE = {
    "NATIVE_API_SECRET": "regular-secret-not-platform-0000001",
    "NATIVE_DOVECOT_POLICY_SECRET": "policy-secret-not-platform-0000002",
    "NATIVE_DB_PASSWORD": "native-db-pass-not-platform-0000003",
    "NATIVE_PLATFORM_SECRET": "dedicated-platform-api-secret-000004",
    "NATIVE_PLATFORM_DB_PASSWORD": "dedicated-platform-db-secret-000005",
    "NATIVE_PLATFORM_OPERATIONS_ENABLED": "True",
    "NATIVE_PLATFORM_DKIM_PREPARE_ENABLED": "True",
}
GOOD = {"domain": "system.matemail.pro", "operation": "prepare_dkim_inactive"}


class DKIMPreparationTests(unittest.TestCase):
    def test_flag_off_denies_before_db_access(self):
        with mock.patch.dict(os.environ, {**BASE, "NATIVE_PLATFORM_DKIM_PREPARE_ENABLED": "False"}, clear=True):
            with mock.patch.object(platform_security.db, "connect_platform") as conn:
                with self.assertRaises(PermissionError):
                    platform_security.prepare_inactive_system_dkim(GOOD)
                conn.assert_not_called()

    def test_unknown_fields_and_other_domains_are_refused(self):
        with mock.patch.dict(os.environ, BASE, clear=True):
            with mock.patch.object(platform_security.db, "connect_platform") as conn:
                for value in ({}, {**GOOD, "rotate": True},
                              {**GOOD, "domain": "mail.matemail.pro"},
                              {**GOOD, "operation": "activate"},
                              None, []):
                    with self.subTest(value=value):
                        with self.assertRaises(ValidationError):
                            platform_security.prepare_inactive_system_dkim(value)
                conn.assert_not_called()

    def test_shared_token_never_authorizes_platform_route(self):
        import app
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/v1/platform/system/prepare-dkim"
        handler.headers = {"X-Native-Api-Secret": BASE["NATIVE_API_SECRET"]}
        handler._send = mock.Mock()
        with mock.patch.dict(os.environ, BASE, clear=True):
            with mock.patch.object(platform_security, "prepare_inactive_system_dkim") as prep:
                handler.do_POST()
        handler._send.assert_called_once_with(403, {"error": "unauthorized"})
        prep.assert_not_called()

    def test_exact_platform_secret_and_route(self):
        import app
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/v1/platform/system/prepare-dkim"
        handler.headers = {"X-Native-Platform-Secret": BASE["NATIVE_PLATFORM_SECRET"]}
        handler._send = mock.Mock()
        handler._body = mock.Mock(return_value=GOOD)
        with mock.patch.dict(os.environ, BASE, clear=True):
            with mock.patch.object(platform_security, "prepare_inactive_system_dkim",
                return_value={"state": "inactive", "credentials_created": False}) as prep:
                handler.do_POST()
        prep.assert_called_once_with(GOOD)
        self.assertEqual(handler._send.call_args.args[0], 200)

    def test_existing_generation_retry_reuses_key_without_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            name = "system.matemail.pro.mm1.g0123456789abcdef.key"
            generation = str(pathlib.Path(directory) / name)
            with mock.patch.dict(os.environ, BASE, clear=True):
                with mock.patch.object(platform_security.db, "connect_platform") as connect, \
                     mock.patch.object(platform_security.db, "dkim_lifecycle_lock"), \
                     mock.patch("dkim.KEY_DIR", pathlib.Path(directory)), \
                     mock.patch("dkim.key_path", return_value=pathlib.Path(directory) / "system.matemail.pro.mm1.key"), \
                     mock.patch("dkim.public_material_of", return_value="PUB"), \
                     mock.patch("dkim.generate") as generate, \
                     mock.patch.object(platform_security.db, "connect_runtime") as runtime, \
                     mock.patch("provisioning._publish_selector_map") as publish:
                    # Already registered row after a previous crash. No new
                    # key may be generated and the existing selector is republished.
                    cur = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
                    cur.fetchone.return_value = ({"selector": "mm1", "public_key": "PUB",
                        "generation_path": generation, "active": True},)
                    result = platform_security.prepare_inactive_system_dkim(GOOD)
                    self.assertEqual(result["state"], "inactive")
                    self.assertFalse(result["mail_delivery_activated"])
                    self.assertIn("system.matemail.pro", result["dns_record_name"])
                    generate.assert_not_called()
                    publish.assert_called_once_with(runtime.return_value.__enter__.return_value)


if __name__ == "__main__":
    unittest.main()
