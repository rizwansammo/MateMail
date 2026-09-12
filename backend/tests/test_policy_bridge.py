"""
The Postfix policy bridge — routing, translation and failure behaviour.

These are unit tests over `decide()` and `to_postfix_action()`. They prove the
bridge asks MateMail the right question at the right stage and never answers in
a way that weakens Postfix. They prove nothing about Postfix itself; that is
`test_engine_policy_integration.py` (configuration) and
`test_policy_protocol_integration.py` (the wire protocol end to end).

The two evidence classes are deliberately kept apart. A Python fake cannot
demonstrate Postfix behaviour, and a test that pretended otherwise would be the
most dangerous kind of green.
"""
import importlib.util
import pathlib
import unittest
from unittest import mock

BRIDGE_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "postfix_policy_bridge.py"
)


def load_bridge():
    """Import the bridge by path — it lives outside the Django package."""
    spec = importlib.util.spec_from_file_location("matemail_policy_bridge", BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load_bridge()


class ActionTranslationTest(unittest.TestCase):
    """MateMail's vocabulary to Postfix's."""

    def test_ok_becomes_dunno_never_ok(self):
        """
        The single most dangerous mistake available in this file.

        Postfix's `OK` means "permit and STOP evaluating this restriction list".
        The entry after ours is `reject_unauth_destination` — the check that
        stops MateMail relaying for domains it does not host. Answering `OK`
        would skip it and make MateMail an open relay for any request this
        service happened to approve.
        """
        self.assertEqual(bridge.to_postfix_action({"action": "OK"}), "DUNNO")

    def test_reject_carries_the_reason(self):
        result = bridge.to_postfix_action({"action": "REJECT", "reason": "Mailbox suspended"})
        self.assertTrue(result.startswith("REJECT "))
        self.assertIn("Mailbox suspended", result)

    def test_defer_uses_defer_if_permit(self):
        """
        `DEFER_IF_PERMIT` rather than a bare `DEFER`, so a relay attempt that a
        later restriction would reject outright is still rejected rather than
        softened into a retryable 4xx.
        """
        result = bridge.to_postfix_action({"action": "DEFER", "reason": "Slow down"})
        self.assertTrue(result.startswith("DEFER_IF_PERMIT"))

    def test_unreachable_matemail_fails_closed(self):
        self.assertTrue(bridge.to_postfix_action(None).startswith("DEFER_IF_PERMIT"))

    def test_an_unrecognised_answer_fails_closed(self):
        """Not understanding the answer is the same situation as not getting one."""
        for payload in ({}, {"action": "MAYBE"}, {"action": ""}, {"nope": 1}):
            with self.subTest(payload=payload):
                self.assertTrue(
                    bridge.to_postfix_action(payload).startswith("DEFER_IF_PERMIT")
                )

    def test_no_translation_ever_yields_a_bare_ok(self):
        for payload in (
            None, {}, {"action": "OK"}, {"action": "REJECT"}, {"action": "DEFER"},
            {"action": "ok"}, {"action": "garbage"},
        ):
            with self.subTest(payload=payload):
                self.assertNotEqual(bridge.to_postfix_action(payload).split()[0], "OK")

    def test_a_multiline_reason_is_flattened(self):
        """
        The protocol is line-oriented. A newline inside a reason would terminate
        the response early and desynchronise the connection.
        """
        result = bridge.to_postfix_action(
            {"action": "REJECT", "reason": "line one\nline two\r\nthree"}
        )
        self.assertNotIn("\n", result)
        self.assertNotIn("\r", result)


class RoutingTest(unittest.TestCase):
    """Which endpoint is asked, at which stage, with which payload."""

    def setUp(self):
        patcher = mock.patch.object(
            bridge, "call_matemail", return_value={"action": "OK", "reason": ""}
        )
        self.call = patcher.start()
        self.addCleanup(patcher.stop)

    def decide(self, **attrs):
        return bridge.decide(attrs)

    # ── Authenticated submission ────────────────────────────────────────────

    def test_rcpt_asks_the_outbound_endpoint_without_counting(self):
        self.decide(
            protocol_state="RCPT",
            sasl_username="alice@acme.example",
            sender="alice@acme.example",
            recipient="someone@elsewhere.example",
        )
        path, payload = self.call.call_args[0]
        self.assertEqual(path, bridge.OUTBOUND_PATH)
        self.assertEqual(payload["stage"], "rcpt")

    def test_end_of_message_asks_the_outbound_endpoint_and_counts(self):
        self.decide(
            protocol_state="END-OF-MESSAGE",
            sasl_username="alice@acme.example",
            sender="alice@acme.example",
        )
        path, payload = self.call.call_args[0]
        self.assertEqual(path, bridge.OUTBOUND_PATH)
        self.assertEqual(payload["stage"], "end_of_data")

    def test_the_end_of_message_state_is_spelled_with_hyphens(self):
        """
        Postfix sends `END-OF-MESSAGE`. An earlier version compared against
        `END_OF_MESSAGE` with underscores, so the only stage that can count a
        message correctly silently fell through to "some other state, pass" and
        the rate limit was never consulted there at all.
        """
        # The underscore spelling is not a Postfix state at all, so it falls
        # through to "nothing to decide yet" — which is exactly the bug: the
        # counting stage was never reached, and nothing anywhere said so.
        action = self.decide(
            protocol_state="END_OF_MESSAGE",
            sasl_username="alice@acme.example",
            sender="alice@acme.example",
        )
        self.call.assert_not_called()
        self.assertEqual(action, "DUNNO")

        # The real spelling reaches the counting stage.
        self.decide(
            protocol_state=bridge.STATE_END_OF_MESSAGE,
            sasl_username="alice@acme.example",
            sender="alice@acme.example",
        )
        _, payload = self.call.call_args[0]
        self.assertEqual(payload["stage"], "end_of_data")
        self.assertEqual(bridge.STATE_END_OF_MESSAGE, "END-OF-MESSAGE")

    def test_one_message_to_many_recipients_counts_once(self):
        """
        RCPT fires once per recipient. Counting there would charge a sender
        five messages for sending one message to five people.
        """
        for recipient in ("a@x.example", "b@x.example", "c@x.example"):
            self.decide(
                protocol_state="RCPT",
                sasl_username="alice@acme.example",
                sender="alice@acme.example",
                recipient=recipient,
            )
        self.decide(
            protocol_state="END-OF-MESSAGE",
            sasl_username="alice@acme.example",
            sender="alice@acme.example",
        )

        stages = [call[0][1]["stage"] for call in self.call.call_args_list]
        self.assertEqual(stages.count("rcpt"), 3)
        self.assertEqual(stages.count("end_of_data"), 1)

    def test_earlier_states_ask_nothing(self):
        for state in ("CONNECT", "EHLO", "MAIL", "DATA", "VRFY"):
            with self.subTest(state=state):
                self.call.reset_mock()
                action = self.decide(
                    protocol_state=state,
                    sasl_username="alice@acme.example",
                    sender="alice@acme.example",
                )
                self.call.assert_not_called()
                self.assertEqual(action, "DUNNO")

    # ── Unauthenticated ─────────────────────────────────────────────────────

    def test_unauthenticated_rcpt_asks_the_inbound_endpoint(self):
        self.decide(
            protocol_state="RCPT",
            sasl_username="",
            sender="someone@outside.example",
            recipient="bob@acme.example",
        )
        path, payload = self.call.call_args[0]
        self.assertEqual(path, bridge.INBOUND_PATH)
        self.assertEqual(payload["recipient"], "bob@acme.example")

    def test_unauthenticated_end_of_message_asks_nothing(self):
        """
        Inbound was decided at RCPT. Asking again here would also drag the
        engine's own internal injections — watchdog probes, quarantine digests —
        into customer policy, and MateMail would answer "domain not hosted
        here" for every one of them.
        """
        action = self.decide(
            protocol_state="END-OF-MESSAGE",
            sasl_username="",
            sender="watchdog@localhost",
        )
        self.call.assert_not_called()
        self.assertEqual(action, "DUNNO")

    def test_a_request_with_no_recipient_asks_nothing(self):
        action = self.decide(protocol_state="RCPT", sasl_username="", recipient="")
        self.call.assert_not_called()
        self.assertEqual(action, "DUNNO")

    def test_the_authenticated_identity_is_taken_from_sasl_not_the_sender(self):
        """
        `sasl_username` is set by Postfix from the SASL layer; `sender` is
        whatever the client typed in MAIL FROM. The routing decision must be
        made on the one the client cannot choose.
        """
        self.decide(
            protocol_state="RCPT",
            sasl_username="alice@acme.example",
            sender="ceo@victim.example",
            recipient="target@elsewhere.example",
        )
        path, payload = self.call.call_args[0]
        self.assertEqual(path, bridge.OUTBOUND_PATH)
        self.assertEqual(payload["sasl_username"], "alice@acme.example")
        self.assertEqual(payload["sender"], "ceo@victim.example")

    def test_a_matemail_outage_defers_and_never_passes(self):
        self.call.return_value = None
        action = self.decide(
            protocol_state="RCPT",
            sasl_username="alice@acme.example",
            sender="alice@acme.example",
            recipient="someone@elsewhere.example",
        )
        self.assertTrue(action.startswith("DEFER_IF_PERMIT"))


class ConfigurationDefaultsTest(unittest.TestCase):
    """The shipped defaults must match the deployed topology."""

    def test_it_addresses_the_backend_over_the_internal_link(self):
        """
        A service name on `matemail_engine_link`, not a host address. A host
        address would mean a published socket, which is the topology DEC-014
        measured and rejected.
        """
        self.assertIn("backend", bridge.DJANGO_INTERNAL_URL)
        for leak in ("127.0.0.1", "localhost", "host.docker.internal", "172.17."):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, bridge.DJANGO_INTERNAL_URL)

    def test_the_request_timeout_is_below_postfix_default_policy_timeout(self):
        """
        Postfix gives a policy service 100s. Timing out first means the caller
        gets MateMail's deliberate DEFER rather than Postfix's generic one.
        """
        self.assertLess(bridge.REQUEST_TIMEOUT, 100)

    def test_the_pass_action_is_dunno(self):
        self.assertEqual(bridge.PASS, "DUNNO")

    def test_the_failure_action_defers(self):
        self.assertTrue(bridge.FAIL_CLOSED.startswith("DEFER_IF_PERMIT"))
