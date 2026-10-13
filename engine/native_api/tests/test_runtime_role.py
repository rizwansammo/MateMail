"""Runtime PostgreSQL role separation for Native Engine request handlers."""
import os
import unittest
from unittest import mock

import db
import app


ENV = {
    "NATIVE_DB_HOST": "disposable-db",
    "NATIVE_DB_NAME": "matemail_engine",
    "NATIVE_DB_USER": "engine",
    "NATIVE_DB_PASSWORD": "disposable-superuser-password-123",
    "NATIVE_RUNTIME_DB_ENABLED": "True",
    "NATIVE_RUNTIME_DB_PASSWORD": "only-runtime-secret-long-enough-12345",
    "NATIVE_API_SECRET": "only-native-http-secret-long-12345",
    "NATIVE_DOVECOT_POLICY_SECRET": "dovecot-policy-secret-long-12345",
    "NATIVE_PLATFORM_SECRET": "different-platform-token-long-12345",
    "NATIVE_PLATFORM_DB_PASSWORD": "different-platform-db-pass-long-12345",
}


class RuntimeRoleTests(unittest.TestCase):
    def test_default_disabled_uses_legacy_connection_only(self):
        with mock.patch.dict(os.environ, {**ENV, "NATIVE_RUNTIME_DB_ENABLED": "False"}, clear=True):
            with mock.patch.object(db, "connect") as old, mock.patch.object(db.psycopg, "connect") as new:
                result = db.connect_runtime()
        self.assertIs(result, old.return_value)
        new.assert_not_called()

    def test_enabled_runtime_connects_as_non_superuser_only(self):
        with mock.patch.dict(os.environ, ENV, clear=True):
            with mock.patch.object(db.psycopg, "connect") as pg:
                db.connect_runtime()
        kwargs = pg.call_args.kwargs
        self.assertEqual(kwargs["user"], "engine_api_runtime")
        self.assertEqual(kwargs["password"], ENV["NATIVE_RUNTIME_DB_PASSWORD"])
        self.assertEqual(kwargs["host"], ENV["NATIVE_DB_HOST"])

    def test_enabled_missing_or_reused_runtime_password_fails_closed(self):
        for value in ("", "short", ENV["NATIVE_DB_PASSWORD"], ENV["NATIVE_API_SECRET"],
                      ENV["NATIVE_PLATFORM_DB_PASSWORD"]):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {**ENV, "NATIVE_RUNTIME_DB_PASSWORD": value}, clear=True):
                    with mock.patch.object(db, "connect") as old, mock.patch.object(db.psycopg, "connect") as pg:
                        with self.assertRaises(RuntimeError):
                            db.connect_runtime()
                    old.assert_not_called()
                    pg.assert_not_called()

    def test_runtime_sync_is_noop_before_schema_nine(self):
        with mock.patch.dict(os.environ, ENV, clear=True):
            with mock.patch.object(db, "current_version", return_value=8):
                with mock.patch.object(db.psycopg, "connect") as pg:
                    db.sync_runtime_credentials(pg.return_value)
                    pg.return_value.cursor.assert_not_called()

    def test_read_only_native_api_uses_runtime_connection(self):
        h = app.Handler.__new__(app.Handler)
        h.path = "/v1/domains"
        h.headers = {"X-Native-Api-Secret": ENV["NATIVE_API_SECRET"]}
        h._query = mock.Mock(return_value={})
        h._send = mock.Mock()
        with mock.patch.dict(os.environ, ENV, clear=True):
            with mock.patch.object(app, "API_SECRET", ENV["NATIVE_API_SECRET"]):
                with mock.patch.object(db, "connect_runtime") as runtime:
                    with mock.patch.dict(app._READ_ROUTES, {"/v1/domains": lambda conn, query: []}):
                        h.do_GET()
        runtime.assert_called_once()
        h._send.assert_called_once_with(200, [])

    def test_write_native_api_uses_runtime_connection(self):
        h = app.Handler.__new__(app.Handler)
        h.path = "/v1/domains/ensure"
        h.headers = {"X-Native-Api-Secret": ENV["NATIVE_API_SECRET"]}
        h._body = mock.Mock(return_value={"name": "customer.example"})
        h._send = mock.Mock()
        with mock.patch.dict(os.environ, ENV, clear=True):
            with mock.patch.object(app, "API_SECRET", ENV["NATIVE_API_SECRET"]):
                with mock.patch.object(db, "connect_runtime") as runtime:
                    with mock.patch.dict(app._WRITE_ROUTES, {"/v1/domains/ensure": lambda conn, body: {"ok": True}}):
                        h.do_POST()
        runtime.assert_called_once()
        h._send.assert_called_once_with(200, {"ok": True})


if __name__ == "__main__":
    unittest.main()
