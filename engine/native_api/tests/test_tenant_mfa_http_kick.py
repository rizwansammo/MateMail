"""Native Tenant Require HTTP transaction and live-session kick contract.

Uses a loopback HTTP server and fake DB transactions; no live mail credentials.
"""
import http.server
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import app
import operations
import kick_recovery

SECRET = "synthetic-tenant-mfa-only"
TENANT = "ef2ac7c9-6c6d-4f5a-a310-0c0aa2db67d8"
MAILBOXES = ("a@example.test", "b@example.test")


class Transaction:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("begin")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append("rollback" if exc_type else "commit")


class TenantNativeMfaHttpTest(unittest.TestCase):
    def setUp(self):
        self.events = []

        def enforce(conn, body):
            self.assertEqual(body["tenant_id"], TENANT)
            self.assertTrue(body["required"])
            self.events.append("enforce")
            return {
                "required": True,
                "mailbox_count": len(MAILBOXES),
                "_kick_addresses": list(MAILBOXES),
            }

        def enqueue(conn, address):
            self.events.append("queue:" + address)
            return (1, address)

        def acknowledge(conn, mailbox_id, generation):
            self.events.append("ack:" + generation)

        self.patches = [
            mock.patch.object(app, "API_SECRET", SECRET),
            mock.patch.dict(app._WRITE_ROUTES, {"/v1/tenants/mfa/require": enforce}),
            mock.patch.object(app.db, "connect_runtime",
                              side_effect=lambda: Transaction(self.events)),
            mock.patch.object(kick_recovery, "enabled", return_value=True),
            mock.patch.object(kick_recovery, "enqueue", side_effect=enqueue),
            mock.patch.object(kick_recovery, "acknowledge", side_effect=acknowledge),
        ]
        for patch in self.patches:
            patch.start()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for patch in reversed(self.patches):
            patch.stop()

    def post(self, *, secret=SECRET):
        payload = json.dumps({
            "tenant_id": TENANT,
            "domains": ["example.test"],
            "required": True,
        }).encode()
        headers = {"Content-Type": "application/json"}
        if secret is not None:
            headers["X-Native-Api-Secret"] = secret
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}/v1/tenants/mfa/require",
            headers=headers, method="POST", data=payload,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_atomic_queue_commit_precedes_imap_disconnect(self):
        with mock.patch.object(operations, "kick_mailbox_sessions",
                               side_effect=lambda addr: self.events.append("kick:" + addr) or 1):
            status, result = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(result, {"required": True, "mailbox_count": 2})
        self.assertEqual(self.events, [
            "begin", "enforce",
            "queue:a@example.test", "queue:b@example.test", "commit",
            "kick:a@example.test", "begin", "ack:a@example.test", "commit",
            "kick:b@example.test", "begin", "ack:b@example.test", "commit",
        ])

    def test_disconnect_failure_returns_unverified_and_preserves_outbox(self):
        def kick(addr):
            self.events.append("kick:" + addr)
            if addr == MAILBOXES[1]:
                raise operations.OperationUnavailable("synthetic doveadm outage")
        with mock.patch.object(operations, "kick_mailbox_sessions", side_effect=kick):
            status, result = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(result.get("code"), "mail_client_disconnect_unverified")
        self.assertIn("ack:a@example.test", self.events)
        self.assertNotIn("ack:b@example.test", self.events)

    def test_kick_recovery_off_rolls_back_without_activating_required(self):
        with mock.patch.object(kick_recovery, "enabled", return_value=False), \
             mock.patch.object(operations, "kick_mailbox_sessions") as kick:
            status, result = self.post()
        self.assertEqual(status, 503)
        self.assertEqual(self.events, ["begin", "enforce", "rollback"])
        kick.assert_not_called()
        self.assertNotIn("saved", result.get("error", "").lower())

    def test_unauthorized_request_does_not_mutate_native_tenant_policy(self):
        with mock.patch.object(operations, "kick_mailbox_sessions") as kick:
            status, result = self.post(secret="not-authorized")
        self.assertEqual(status, 403)
        self.assertEqual(result["error"], "unauthorized")
        self.assertEqual(self.events, [])
        kick.assert_not_called()


if __name__ == "__main__":
    unittest.main()
