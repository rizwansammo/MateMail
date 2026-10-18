"""Stage 7A: revoke/disable MUST disconnect already authenticated IMAP sessions."""
import unittest
from unittest import mock

import operations
import app


class DovecotSessionKickTest(unittest.TestCase):
    def test_exact_mailbox_only_and_force(self):
        with mock.patch.object(
            operations, "_doveadm",
            return_value=[["doveadmResponse", [], "ne4"]],
        ) as transport:
            self.assertEqual(
                operations.kick_mailbox_sessions("alice@example.test"), 1
            )
            transport.assert_called_once_with(
                "kick", {"mask": "alice@example.test", "force": True},
            )

    def test_broad_mask_forbidden(self):
        with mock.patch.object(operations, "_doveadm") as transport:
            with self.assertRaises(Exception):
                operations.kick_mailbox_sessions("*@example.test")
            transport.assert_not_called()

    def test_admin_api_outage_fails_not_fake_success(self):
        with mock.patch.object(
            operations, "_doveadm",
            side_effect=operations.OperationUnavailable("offline"),
        ):
            with self.assertRaises(operations.OperationUnavailable):
                operations.kick_mailbox_sessions("alice@example.test")

    def test_invalid_admin_reply_is_a_failure(self):
        for response in ([], [["error", ["bad command"], "ne4"]], None):
            with self.subTest(response=response):
                with mock.patch.object(
                    operations, "_doveadm", return_value=response,
                ):
                    with self.assertRaises(operations.OperationUnavailable):
                        operations.kick_mailbox_sessions("alice@example.test")

    def test_commit_precedes_dovecot_disconnect(self):
        """The DB must reject re-auth even when doveadm administrative API fails."""
        events = []

        class Connection:
            def __enter__(self):
                events.append("begin")
                return self
            def __exit__(self, exc_type, exc, tb):
                events.append("commit" if exc_type is None else "rollback")

        class FakeHandler:
            _route = "/v1/mailboxes/client/revoke"
            command = "POST"
            path = "/v1/mailboxes/client/revoke"
            headers = {}
            def _body(self):
                return {"address": "alice@example.test", "id": "opaque-id"}
            def _send(self, status, payload):
                events.append(("response", status))
            def _fail(self, exc):
                events.append(("failure", type(exc).__name__))

        def mutate(conn, data):
            events.append("revoke")
            return {"revoked": True}
        def kick(address):
            events.append("kick")
            return 1

        with mock.patch.object(app, "authorized", return_value=True), \
             mock.patch.dict(app._WRITE_ROUTES, {
                 "/v1/mailboxes/client/revoke": mutate,
             }), \
             mock.patch.object(app.db, "connect_runtime", side_effect=Connection), \
             mock.patch.object(app.operations, "kick_mailbox_sessions",
                               side_effect=kick):
            app.Handler.do_POST(FakeHandler())
        self.assertEqual(events, [
            "begin", "revoke", "commit", "kick", ("response", 200),
        ])

    def test_doveadm_outage_does_not_cancel_committed_revocation(self):
        events = []

        class Connection:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                events.append("committed")

        class FakeHandler:
            _route = "/v1/mailboxes/client/revoke"
            command = "POST"
            path = "/v1/mailboxes/client/revoke"
            headers = {}
            def _body(self):
                return {"address": "alice@example.test", "id": "opaque-id"}
            def _send(self, status, payload):
                events.append(("response", status))
            def _fail(self, exc):
                events.append(("failure", type(exc).__name__))

        def mutate(conn, data):
            events.append("revoked")
            return {"revoked": True}
        with mock.patch.object(app, "authorized", return_value=True), \
             mock.patch.dict(app._WRITE_ROUTES, {
                 "/v1/mailboxes/client/revoke": mutate,
             }), \
             mock.patch.object(app.db, "connect_runtime", side_effect=Connection), \
             mock.patch.object(app.operations, "kick_mailbox_sessions",
                               side_effect=operations.OperationUnavailable("down")):
            app.Handler.do_POST(FakeHandler())
        self.assertEqual(events[:2], ["revoked", "committed"])
        self.assertTrue(
            ("failure", "OperationUnavailable") in events or
            ("response", 503) in events
        )

    def test_revoke_and_policy_are_kick_scoped(self):
        # Regression contract: no other administrative operation can silently
        # disconnect all tenant connections.
        route_definitions = app._WRITE_ROUTES
        self.assertIn("/v1/mailboxes/client/revoke", route_definitions)
        self.assertIn("/v1/mailboxes/client/policy", route_definitions)
        self.assertNotIn("/v1/mailboxes/client/kick-all", route_definitions)


if __name__ == "__main__":
    unittest.main()
