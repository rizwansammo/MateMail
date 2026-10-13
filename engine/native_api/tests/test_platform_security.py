"""No tenant token, unsafe payload or inactive configuration can stage system mail."""
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import platform_security
from validation import ValidationError


BASELINE = {
    "NATIVE_API_SECRET": "ordinary-provisioning-secret-long-123",
    "NATIVE_DOVECOT_POLICY_SECRET": "dovecot-policy-secret-long-123456",
    "NATIVE_DB_PASSWORD": "normal-database-secret-long-123",
    "NATIVE_PLATFORM_SECRET": "only-platform-operations-secret-long-123",
    "NATIVE_PLATFORM_DB_PASSWORD": "only-platform-postgres-secret-long-123",
    "NATIVE_PLATFORM_OPERATIONS_ENABLED": "True",
}


class PlatformSecurityTests(unittest.TestCase):
    def test_default_disabled_does_not_admit_valid_token(self):
        with mock.patch.dict(os.environ, BASELINE | {"NATIVE_PLATFORM_OPERATIONS_ENABLED": "False"}, clear=True):
            self.assertFalse(platform_security.authorized(SimpleNamespace(
                headers={"X-Native-Platform-Secret": BASELINE["NATIVE_PLATFORM_SECRET"]}
            )))

    def test_generic_native_and_dovecot_credentials_have_no_access(self):
        with mock.patch.dict(os.environ, BASELINE, clear=True):
            for header in (
                {"X-Native-Api-Secret": BASELINE["NATIVE_API_SECRET"]},
                {"X-Native-Platform-Secret": BASELINE["NATIVE_API_SECRET"]},
                {"X-Native-Platform-Secret": BASELINE["NATIVE_DOVECOT_POLICY_SECRET"]},
                {},
            ):
                self.assertFalse(platform_security.authorized(SimpleNamespace(headers=header)))
            self.assertTrue(platform_security.authorized(SimpleNamespace(headers={
                "X-Native-Platform-Secret": BASELINE["NATIVE_PLATFORM_SECRET"],
            })))

    def test_reused_or_short_secrets_are_not_allowed(self):
        with mock.patch.dict(os.environ, BASELINE | {
            "NATIVE_PLATFORM_SECRET": BASELINE["NATIVE_API_SECRET"]
        }, clear=True):
            self.assertFalse(platform_security.credentials_distinct())
        with mock.patch.dict(os.environ, BASELINE | {
            "NATIVE_PLATFORM_DB_PASSWORD": BASELINE["NATIVE_DB_PASSWORD"]
        }, clear=True):
            self.assertFalse(platform_security.credentials_distinct())
        with mock.patch.dict(os.environ, BASELINE | {
            "NATIVE_PLATFORM_SECRET": "short"
        }, clear=True):
            self.assertFalse(platform_security.credentials_distinct())

    def test_exact_inactive_stage_payload_only(self):
        good = {"domain": "system.matemail.pro", "operation": "stage_inactive"}
        with mock.patch.dict(os.environ, BASELINE, clear=True):
            with mock.patch.object(platform_security.db, "connect_platform") as connect:
                for payload in (
                    {}, {"domain": "mail.matemail.pro", "operation": "stage_inactive"},
                    {**good, "password": "forbidden"}, {**good, "activate": True},
                    {**good, "mailboxes": ["all"]}, None, [],
                ):
                    with self.subTest(payload=payload):
                        with self.assertRaises(ValidationError):
                            platform_security.stage_inactive_system_mail(payload)
                connect.assert_not_called()

    def test_disabled_staging_never_reaches_database(self):
        with mock.patch.dict(os.environ, BASELINE | {
            "NATIVE_PLATFORM_OPERATIONS_ENABLED": "False"
        }, clear=True):
            with mock.patch.object(platform_security.db, "connect_platform") as conn:
                with self.assertRaises(PermissionError):
                    platform_security.stage_inactive_system_mail({
                        "domain": "system.matemail.pro", "operation": "stage_inactive",
                    })
                conn.assert_not_called()

    def test_stage_invokes_only_scoped_database_function(self):
        result = {"state": "inactive", "delivery_activated": False}
        with mock.patch.dict(os.environ, BASELINE, clear=True):
            with mock.patch.object(platform_security.db, "connect_platform") as connect:
                connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (result,)
                response = platform_security.stage_inactive_system_mail({
                    "domain": "system.matemail.pro", "operation": "stage_inactive",
                })
                self.assertEqual(response, result)
                cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
                cursor.execute.assert_called_once_with("SELECT public.platform_stage_system_mail()")
                connect.return_value.__enter__.return_value.commit.assert_called_once()

    def test_native_http_router_never_accepts_normal_secret(self):
        import app
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/v1/platform/system/stage"
        handler.headers = {
            "X-Native-Api-Secret": BASELINE["NATIVE_API_SECRET"],
        }
        handler._send = mock.Mock()
        with mock.patch.dict(os.environ, BASELINE, clear=True):
            with mock.patch.object(platform_security, "stage_inactive_system_mail") as fn:
                handler.do_POST()
        handler._send.assert_called_once_with(403, {"error": "unauthorized"})
        fn.assert_not_called()

    def test_native_http_router_accepts_dedicated_token_only(self):
        import app
        handler = app.Handler.__new__(app.Handler)
        handler.path = "/v1/platform/system/stage"
        handler.headers = {
            "X-Native-Platform-Secret": BASELINE["NATIVE_PLATFORM_SECRET"],
        }
        handler._send = mock.Mock()
        handler._body = mock.Mock(return_value={
            "domain": "system.matemail.pro", "operation": "stage_inactive"
        })
        with mock.patch.dict(os.environ, BASELINE, clear=True):
            with mock.patch.object(platform_security, "stage_inactive_system_mail", return_value={
                "state": "inactive", "delivery_activated": False,
            }) as fn:
                handler.do_POST()
        fn.assert_called_once()
        self.assertEqual(handler._send.call_args.args[0], 200)


if __name__ == "__main__":
    unittest.main()
