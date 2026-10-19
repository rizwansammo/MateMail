"""Stage 7C-2: real Native HTTP control-plane path, without a real DB.

Unlike Handler.do_POST mock objects, this exercises an actual loopback HTTP
server, request parsing, X-Native-Api-Secret auth, transaction sequencing,
active-session revocation acknowledgements, and the retry-after-503 path.
The database connection is deliberately simulated; do NOT count this as
the real Dovecot/Postfix integration test.
"""
import contextlib
import http.server
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import app
import operations

SECRET = "stage7c-loopback-only"
TARGET = "alice@synthetic.invalid"


class Connection:
    def __init__(self, events):
        self.events = events
    def __enter__(self):
        self.events.append("begin")
        return self
    def __exit__(self, exc_type, exc, tb):
        self.events.append("rollback" if exc_type else "commit")


class NativeHTTPRevocationTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.deleted = False
        def revoke(_conn, body):
            self.events.append("revoke")
            self.assertEqual(body["address"], TARGET)
            # Match Native DB revocation idempotency: second time already revoked.
            was_deleted = self.deleted
            self.deleted = True
            return {"revoked": not was_deleted}
        self.patches = [
            mock.patch.object(app, "API_SECRET", SECRET),
            mock.patch.dict(app._WRITE_ROUTES, {
                "/v1/mailboxes/client/revoke": revoke,
            }),
            mock.patch.object(app.db, "connect_runtime",
                              side_effect=lambda: Connection(self.events)),
        ]
        for patch in self.patches:
            patch.start()
        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), app.Handler,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True,
        )
        self.thread.start()
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for patch in reversed(self.patches):
            patch.stop()

    def post(self, payload=None, *, secret=SECRET):
        data = json.dumps(payload or {"address": TARGET, "id": "opaque-id"}).encode()
        headers = {"Content-Type": "application/json"}
        if secret is not None:
            headers["X-Native-Api-Secret"] = secret
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}/v1/mailboxes/client/revoke",
            data=data, method="POST", headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_revocation_commits_before_http_session_kick(self):
        with mock.patch.object(
            operations, "kick_mailbox_sessions",
            side_effect=lambda address: self.events.append("kick") or 1,
        ) as kick:
            status, data = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(data, {"revoked": True})
        self.assertEqual(self.events, ["begin", "revoke", "commit", "kick"])
        kick.assert_called_once_with(TARGET)

    def test_admin_outage_is_503_and_idempotent_retry_can_kick(self):
        counts = {"calls": 0}
        def outage_once(address):
            self.assertEqual(address, TARGET)
            self.events.append("kick-attempt")
            counts["calls"] += 1
            if counts["calls"] == 1:
                raise operations.OperationUnavailable("synthetic outage")
            return 1
        with mock.patch.object(
            operations, "kick_mailbox_sessions", side_effect=outage_once,
        ):
            first_status, first = self.post()
            second_status, second = self.post()
        self.assertEqual(first_status, 503, first)
        self.assertIn("disconnect unverified", first["error"])
        self.assertEqual(first["code"], "mail_client_disconnect_unverified")
        self.assertEqual(second_status, 200, second)
        self.assertEqual(second, {"revoked": False})
        self.assertEqual(self.events, [
            "begin", "revoke", "commit", "kick-attempt",
            "begin", "revoke", "commit", "kick-attempt",
        ])

    def test_wrong_secret_cannot_mutate_or_kick(self):
        with mock.patch.object(operations, "kick_mailbox_sessions") as kick:
            for secret in (None, "incorrect-secret"):
                status, body = self.post(secret=secret)
                self.assertEqual(status, 403, body)
                self.assertEqual(body.get("error"), "unauthorized")
            kick.assert_not_called()
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
