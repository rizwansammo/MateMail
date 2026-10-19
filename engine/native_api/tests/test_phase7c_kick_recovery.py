"""Stage 7C recovery outbox: network-free, failure/race contract tests."""
import contextlib
import os
import threading
import unittest
from unittest import mock

import app
import kick_recovery
import operations
from validation import ValidationError


class Cursor:
    def __init__(self, results=()):
        self.results = list(results)
        self.sql = []
    def execute(self, query, params=None):
        self.sql.append((query, params))
    def fetchone(self):
        return self.results.pop(0) if self.results else None
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class Connection:
    def __init__(self, results=()):
        self.cur = Cursor(results)
    def cursor(self):
        return self.cur


class RecoveryOutboxTest(unittest.TestCase):
    def test_recovery_gate_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(kick_recovery.enabled())

    def test_enqueue_coalesces_and_rotates_generation_inside_caller_transaction(self):
        c = Connection([(13, "99999999-9999-4999-8999-999999999999")])
        self.assertEqual(kick_recovery.enqueue(c, "alice@example.test"),
                         (13, "99999999-9999-4999-8999-999999999999"))
        query, params = c.cur.sql[0]
        self.assertIn("ON CONFLICT (mailbox_id) DO UPDATE", query)
        self.assertIn("generation=gen_random_uuid()", query)
        self.assertEqual(params, ("alice@example.test",))

    def test_enqueue_normalizes_valid_mailbox_before_query(self):
        c = Connection([(13, "99999999-9999-4999-8999-999999999999")])
        kick_recovery.enqueue(c, "  ALICE@Example.TEST  ")
        query, params = c.cur.sql[0]
        self.assertEqual(params, ("alice@example.test",))

    def test_enqueue_rejects_invalid_mailbox_identifier(self):
        with self.assertRaises(ValidationError):
            kick_recovery.enqueue(Connection(), "*.example.test")

    def test_missing_mailbox_fails_closed(self):
        with self.assertRaises(RuntimeError):
            kick_recovery.enqueue(Connection(), "gone@example.test")

    def test_ack_guarded_by_mailbox_and_exact_generation(self):
        c = Connection()
        kick_recovery.acknowledge(c, 18, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        query, params = c.cur.sql[0]
        self.assertIn("generation=%s::uuid", query)
        self.assertEqual(params, (18, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))

    def test_backoff_is_bounded(self):
        self.assertEqual(kick_recovery.backoff_seconds(0), 5)
        self.assertEqual(kick_recovery.backoff_seconds(2), 20)
        self.assertEqual(kick_recovery.backoff_seconds(200), 3600)

    def test_worker_ignores_empty_queue(self):
        c = Connection([None])
        with mock.patch.object(operations, "kick_mailbox_sessions") as kick:
            self.assertFalse(kick_recovery.recover_one(c))
            kick.assert_not_called()

    def test_worker_acknowledges_only_its_generation(self):
        row = (12, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "alice@example.test", 0)
        c = Connection([row])
        with mock.patch.object(operations, "kick_mailbox_sessions", return_value=1) as kick:
            self.assertTrue(kick_recovery.recover_one(c))
        kick.assert_called_once_with("alice@example.test")
        self.assertEqual(len(c.cur.sql), 2)
        self.assertIn("FOR UPDATE OF q SKIP LOCKED", c.cur.sql[0][0])
        self.assertIn("DELETE FROM public.mail_client_kick_pending", c.cur.sql[1][0])
        self.assertEqual(c.cur.sql[1][1], (12, row[1]))

    def test_worker_failure_keeps_queue_and_increments_backoff(self):
        row = (12, "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "alice@example.test", 2)
        c = Connection([row])
        with mock.patch.object(operations, "kick_mailbox_sessions",
                               side_effect=operations.OperationUnavailable("down")):
            self.assertTrue(kick_recovery.recover_one(c))
        self.assertEqual(len(c.cur.sql), 2)
        self.assertIn("attempts=attempts+1", c.cur.sql[1][0])
        self.assertNotIn("DELETE", c.cur.sql[1][0])
        self.assertEqual(c.cur.sql[1][1], (20, 12, row[1]))

    def test_status_returns_aggregate_only_without_mailbox_identity(self):
        c = Connection([(4, 2, 7, 231)])
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_KICK_RECOVERY_ENABLED": "true"}):
            response = kick_recovery.recovery_status(c)
        self.assertEqual(response, {
            "enabled": True, "pending": 4, "due": 2,
            "max_attempts": 7, "oldest_pending_seconds": 231,
        })
        self.assertNotIn("mailbox", str(response).lower())
        query, params = c.cur.sql[0]
        self.assertIn("mail_client_kick_pending", query)
        self.assertNotIn("JOIN public.mailbox", query)
        self.assertIsNone(params)

    def test_status_works_when_recovery_flag_off_to_surface_stuck_jobs(self):
        c = Connection([(2, 0, 5, 120)])
        with mock.patch.dict(os.environ, {"NATIVE_CLIENT_KICK_RECOVERY_ENABLED": "false"}):
            response = kick_recovery.recovery_status(c)
        self.assertFalse(response["enabled"])
        self.assertEqual(response["pending"], 2)

    def test_worker_survives_connection_failure(self):
        stopped = threading.Event()
        attempts = []
        def bad_connection():
            attempts.append(1)
            raise ConnectionError("synthetic db down")
        def wait_and_stop(seconds):
            stopped.set()
        with mock.patch.object(stopped, "wait", side_effect=wait_and_stop):
            kick_recovery.run_worker(bad_connection, stopped)
        self.assertEqual(len(attempts), 1)


class OperatorRecoveryStatusRouteTest(unittest.TestCase):
    @staticmethod
    def request(events):
        class FakeHandler:
            _route = "/v1/mailboxes/client/recovery/status"
            headers = {}
            def _query(self):
                return {}
            def _send(self, status, payload):
                events.append((status, payload))
            def _fail(self, exc):
                raise AssertionError(f"Unexpected error: {type(exc).__name__}")
        return FakeHandler()

    def test_status_requires_native_api_secret(self):
        events = []
        with mock.patch.object(app, "authorized", return_value=False), \
             mock.patch.object(app.db, "connect_runtime") as connect:
            app.Handler.do_GET(self.request(events))
        self.assertEqual(events, [(403, {"error": "unauthorized"})])
        connect.assert_not_called()

    def test_authorized_status_exposes_only_aggregates(self):
        events = []
        from contextlib import nullcontext
        payload = {
            "enabled": False, "pending": 3, "due": 1,
            "max_attempts": 2, "oldest_pending_seconds": 60,
        }
        with mock.patch.object(app, "authorized", return_value=True), \
             mock.patch.object(app.db, "connect_runtime", return_value=nullcontext(object())), \
             mock.patch.object(kick_recovery, "recovery_status", return_value=payload):
            app.Handler.do_GET(self.request(events))
        self.assertEqual(events, [(200, payload)])
        self.assertNotIn("alice", str(events).lower())


class NativeHandlerOutboxContractTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.secret_patch = mock.patch.object(app, "authorized", return_value=True)
        self.secret_patch.start()
        self.flag_patch = mock.patch.object(kick_recovery, "enabled", return_value=True)
        self.flag_patch.start()

    def tearDown(self):
        self.flag_patch.stop()
        self.secret_patch.stop()

    def handler(self):
        events = self.events
        class FakeHandler:
            _route = "/v1/mailboxes/client/revoke"
            command = "POST"
            path = "/v1/mailboxes/client/revoke"
            headers = {}
            def _body(self):
                return {"address": "alice@example.test", "id": "only-synthetic"}
            def _send(self, code, payload):
                events.append(("http", code, payload.get("code")))
            def _fail(self, error):
                events.append(("error", type(error).__name__))
        return FakeHandler()

    def test_queue_commit_precedes_kick_and_ack_follows(self):
        events = self.events
        class Db:
            def __enter__(self):
                events.append("db-open")
                return self
            def __exit__(self, kind, exc, tb):
                events.append("db-rollback" if kind else "db-commit")

        with mock.patch.dict(app._WRITE_ROUTES, {"/v1/mailboxes/client/revoke":
                 lambda c, body: events.append("mutation") or {"revoked": True}}), \
             mock.patch.object(app.db, "connect_runtime", side_effect=Db), \
             mock.patch.object(kick_recovery, "enqueue",
                               side_effect=lambda c, a: events.append("enqueue") or (1, "uuid")) as enqueue, \
             mock.patch.object(operations, "kick_mailbox_sessions",
                               side_effect=lambda a: events.append("kick")) as kick, \
             mock.patch.object(kick_recovery, "acknowledge",
                               side_effect=lambda c, mid, gen: events.append("ack")):
            app.Handler.do_POST(self.handler())
        self.assertEqual(events, [
            "db-open", "mutation", "enqueue", "db-commit",
            "kick", "db-open", "ack", "db-commit", ("http", 200, None),
        ])
        enqueue.assert_called_once()
        kick.assert_called_once_with("alice@example.test")

    def test_failed_kick_keeps_durable_job_and_returns_partial_commit(self):
        events = self.events
        class Db:
            def __enter__(self):
                return self
            def __exit__(self, kind, exc, tb):
                events.append("commit" if kind is None else "rollback")

        with mock.patch.dict(app._WRITE_ROUTES, {"/v1/mailboxes/client/revoke":
                 lambda c, body: {"revoked": True}}), \
             mock.patch.object(app.db, "connect_runtime", side_effect=Db), \
             mock.patch.object(kick_recovery, "enqueue", return_value=(1, "uuid")), \
             mock.patch.object(operations, "kick_mailbox_sessions",
                               side_effect=operations.OperationUnavailable("down")), \
             mock.patch.object(kick_recovery, "acknowledge") as ack:
            app.Handler.do_POST(self.handler())
        self.assertEqual(events, [
            "commit", ("http", 503, "mail_client_disconnect_unverified"),
        ])
        ack.assert_not_called()


    def test_enqueue_failure_rolls_back_mutation_and_does_not_disconnect(self):
        events = self.events
        class Db:
            def __enter__(self):
                return self
            def __exit__(self, kind, exc, tb):
                events.append("rollback" if kind else "commit")
        with mock.patch.dict(app._WRITE_ROUTES, {"/v1/mailboxes/client/revoke":
                 lambda c, body: events.append("mutation") or {"revoked": True}}), \
             mock.patch.object(app.db, "connect_runtime", side_effect=Db), \
             mock.patch.object(kick_recovery, "enqueue",
                               side_effect=RuntimeError("synthetic outbox unavailable")), \
             mock.patch.object(operations, "kick_mailbox_sessions") as kick:
            app.Handler.do_POST(self.handler())
        self.assertEqual(events, [
            "mutation", "rollback", ("error", "RuntimeError"),
        ])
        kick.assert_not_called()

    def test_postcommit_ack_failure_cannot_claim_success(self):
        events = self.events
        class Db:
            def __enter__(self):
                return self
            def __exit__(self, kind, exc, tb):
                events.append("rollback" if kind else "commit")
        with mock.patch.dict(app._WRITE_ROUTES, {"/v1/mailboxes/client/revoke":
                 lambda c, body: {"revoked": True}}), \
             mock.patch.object(app.db, "connect_runtime", side_effect=Db), \
             mock.patch.object(kick_recovery, "enqueue", return_value=(1, "uuid")), \
             mock.patch.object(operations, "kick_mailbox_sessions", return_value=1), \
             mock.patch.object(kick_recovery, "acknowledge",
                               side_effect=RuntimeError("synthetic ack failure")):
            app.Handler.do_POST(self.handler())
        self.assertEqual(events, [
            "commit", "rollback",
            ("http", 503, "mail_client_disconnect_unverified"),
        ])

if __name__ == "__main__":
    unittest.main()
