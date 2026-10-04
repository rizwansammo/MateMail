"""
Native Engine half of PostBox push: the Dovecot hook and the API relay.

What is proved here, against the real files rather than a description of them:

- the hook is loaded for LMTP only, and only when its own secret is set;
- the Lua script reads a message's identity and nothing of its content, sends
  once with a one-second limit and only after a successful commit;
- Dovecot gains one credential and no network, port or database privilege;
- the API refuses a report without Dovecot's push secret or with any content,
  derives event ids deterministically, and relays without ever blocking.

The pinned Dovecot 2.4.1 image itself was exercised end to end (delivery,
API down, API stalled, non-delivery save); those measurements are recorded in
docs/POSTBOX_REMOTE_PUSH.md because they need Docker, not this suite.
"""
import http.server
import inspect
import json
import pathlib
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
NE = REPO / "deploy" / "native-engine"
ENGINE_DIR = REPO / "engine" / "native_api"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import push as engine_push  # noqa: E402  (the engine's module, not apps.postbox.push)
from validation import ValidationError  # noqa: E402

DOVECOT_CONF = (NE / "dovecot" / "dovecot.conf").read_text(encoding="utf-8")
ENTRYPOINT = (NE / "images" / "dovecot" / "entrypoint.sh").read_text(encoding="utf-8")
LUA = (NE / "dovecot" / "postbox-push.lua").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load((NE / "docker-compose.yml").read_text(encoding="utf-8"))


def code_only(text: str, comment: str) -> str:
    """Strip comment lines, so prose cannot satisfy or trip a test."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(comment))


class DovecotPushHookTest(unittest.TestCase):

    def test_the_hook_loads_for_lmtp_and_imap_only_with_its_secret(self):
        conf = code_only(DOVECOT_CONF, "#")
        # `_try`: config ships before the image that renders it, and a missing
        # optional include must cost the push, not Dovecot (measured).
        self.assertIn("!include_try engine-push.conf", conf)
        self.assertNotRegex(conf, r"!include\s+engine-push\.conf")
        # The global plugin list is unchanged: push is protocol-scoped.
        global_plugins = re.search(r"^mail_plugins \{(.*?)\}", conf, re.S | re.M).group(1)
        self.assertNotIn("push_notification", global_plugins)
        self.assertNotIn("lua", global_plugins)

        entry = code_only(ENTRYPOINT, "#")
        rendered = entry.split('if [ -n "${NATIVE_DOVECOT_PUSH_SECRET:-}" ]; then', 1)[1]
        rendered, disabled = rendered.split("\nelse\n", 1)
        self.assertLess(rendered.index('echo "protocol lmtp {"'), rendered.index("push_notification = yes"))
        self.assertIn('echo "protocol imap {"', rendered)
        for plugin in ("notify", "push_notification", "mail_lua", "push_notification_lua"):
            self.assertIn(f'echo "    {plugin} = yes"', rendered)
        # The boolean-list form ADDS to the global list (quota stays loaded for
        # LMTP - measured on 2.4.1); an assignment would be a different thing.
        self.assertIn('echo "  mail_plugins {"', rendered)
        self.assertNotRegex(rendered, r"mail_plugins\s*=")
        self.assertIn('echo "  push_notification_driver = lua"', rendered)
        self.assertIn('echo "  lua_file = /etc/dovecot/postbox-push.lua"', rendered)
        self.assertIn("exit 78", rendered, "an unrepresentable secret refuses to start")
        # Without the secret the include is a comment: no plugin, no hook.
        disabled = disabled.split("\nfi\n", 1)[0]
        self.assertIn("> /etc/dovecot/engine-push.conf", disabled)
        self.assertNotIn("mail_plugins", disabled)

    def test_the_script_reads_the_message_identity_and_nothing_of_its_content(self):
        code = code_only(LUA, "--")
        self.assertEqual({"mailbox", "uid_validity", "uid", "event"}, set(re.findall(r"\bevent\.(\w+)", code)) - {"folder"})
        for content in ("subject", "snippet", "from_address", "to_address", "message_id",
                        "event.from", "event.to", "from_display_name"):
            self.assertNotIn(content, code)
        # Only event kind plus the four identity fields are ever encoded.
        payload = re.search(r"json\.encode\(\{(.*?)\}\)", code, re.S).group(1)
        self.assertEqual({"event", "mailbox", "folder", "uid_validity", "uid"},
                         set(re.findall(r"(\w+)\s*=", payload)))
        # No shell, file or process access from inside LMTP.
        for dangerous in ("os.execute", "io.open", "io.popen", "require(\"posix\")"):
            self.assertNotIn(dangerous, code)

    def test_the_script_sends_once_quickly_and_only_after_a_commit(self):
        code = code_only(LUA, "--")
        self.assertIn('request_absolute_timeout = "1s"', code)
        self.assertIn("request_max_attempts = 1", code)
        self.assertIn("request_max_redirects = 0", code)
        self.assertRegex(code, r"function dovecot_lua_notify_end_txn\(ctx, success\)\s+if not success")
        self.assertIn("pcall(send", code, "a failure is caught, never raised into LMTP")
        self.assertIn('"X-Native-Push-Secret"', code)
        handlers = set(re.findall(r"function (dovecot_lua_notify_event_\w+)", code))
        self.assertEqual({
            "dovecot_lua_notify_event_message_new",
            "dovecot_lua_notify_event_flags_set",
            "dovecot_lua_notify_event_flags_clear",
            "dovecot_lua_notify_event_message_append",
            "dovecot_lua_notify_event_message_trash",
            "dovecot_lua_notify_event_message_expunge",
        }, handlers)


class DovecotPushIsolationTest(unittest.TestCase):

    def test_dovecot_gains_one_credential_and_no_network_port_or_database_power(self):
        dovecot = COMPOSE["services"]["dovecot"]
        self.assertNotIn("matemail_engine_link", str(dovecot["networks"]))
        self.assertEqual(["${NATIVE_PUBLIC_IP:?NATIVE_PUBLIC_IP must be set; an empty value "
                          "binds every interface}:993:993"], dovecot["ports"])
        env = dovecot["environment"]
        self.assertEqual("${NATIVE_DOVECOT_PUSH_SECRET:-}", env["NATIVE_DOVECOT_PUSH_SECRET"])
        for forbidden in ("NATIVE_DB_PASSWORD", "NATIVE_API_SECRET", "NATIVE_POSTBOX_PUSH_SECRET",
                          "NATIVE_POSTBOX_PUSH_URL", "POSTBOX_PUSH_INGEST_SECRET"):
            self.assertNotIn(forbidden, env)
        self.assertEqual("engine_ro_dovecot", env["NATIVE_DOVECOT_DB_USER"])
        self.assertIn("./dovecot/postbox-push.lua:/etc/dovecot/postbox-push.lua:ro", dovecot["volumes"])

        api = COMPOSE["services"]["api"]["environment"]
        for name in ("NATIVE_DOVECOT_PUSH_SECRET", "NATIVE_POSTBOX_PUSH_URL",
                     "NATIVE_POSTBOX_PUSH_SECRET"):
            self.assertEqual(f"${{{name}:-}}", api[name], "optional, never defaulted")
        # Three credentials, three names: none is the provisioning or policy secret.
        self.assertEqual(3, len({"NATIVE_DOVECOT_PUSH_SECRET", "NATIVE_POSTBOX_PUSH_SECRET",
                                 "NATIVE_API_SECRET"}))


class NativeApiPushTest(unittest.TestCase):

    def report(self, **overrides):
        return {"mailbox": "Alice@Acme.test", "folder": "INBOX",
                "uid_validity": 1790364354, "uid": 7, **overrides}

    def test_a_report_needs_dovecots_push_secret(self):
        self.assertIn("compare_digest", inspect.getsource(engine_push.dovecot_authorized))
        with mock.patch.object(engine_push, "DOVECOT_PUSH_SECRET", ""):
            self.assertFalse(engine_push.dovecot_authorized(""))
        with mock.patch.object(engine_push, "DOVECOT_PUSH_SECRET", "test-only-dovecot-push"):
            self.assertFalse(engine_push.dovecot_authorized(""))
            self.assertFalse(engine_push.dovecot_authorized("test-only-policy-secret"))
            self.assertTrue(engine_push.dovecot_authorized("test-only-dovecot-push"))
        # The route is checked before, and never with, the provisioning secret.
        app_source = (ENGINE_DIR / "app.py").read_text(encoding="utf-8")
        post = app_source.split("def do_POST(self):", 1)[1]
        self.assertLess(post.index('"/v1/dovecot/push"'), post.index("if not authorized(self)"))

        # And through the API's real HTTP handler, on loopback: refused without
        # the secret or with content, and answered at once when valid - the
        # relay is only handed the event.
        import app as engine_app

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), engine_app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/dovecot/push"

        def call(body, secret):
            request = urllib.request.Request(
                url, data=json.dumps(body).encode(), method="POST",
                headers={"Content-Type": "application/json", "X-Native-Push-Secret": secret})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

        relay = mock.Mock(submit=mock.Mock(return_value=True))
        with mock.patch.object(engine_push, "DOVECOT_PUSH_SECRET", "test-only-dovecot-push"), \
             mock.patch.object(engine_push, "relay", relay):
            self.assertEqual(403, call(self.report(), "test-only-wrong")[0])
            self.assertEqual(400, call(self.report(subject="Synthetic"), "test-only-dovecot-push")[0])
            self.assertEqual((202, {"queued": True}), call(self.report(), "test-only-dovecot-push"))
        relay.submit.assert_called_once()
        self.assertEqual("alice@acme.test", relay.submit.call_args.args[0]["mailbox"])

    def test_a_report_is_strict_and_its_event_id_is_deterministic(self):
        event = engine_push.new_mail_event(self.report())
        self.assertEqual({"event_id", "event", "mailbox", "folder", "uid_validity", "uid"},
                         set(event))
        self.assertEqual(("new_mail", "alice@acme.test"), (event["event"], event["mailbox"]))
        self.assertEqual(event["event_id"], engine_push.new_mail_event(self.report())["event_id"])
        for changed in ({"uid": 8}, {"uid_validity": 1790364355}, {"folder": "Archive"},
                        {"mailbox": "bob@acme.test"}):
            with self.subTest(changed=changed):
                self.assertNotEqual(event["event_id"],
                                    engine_push.new_mail_event(self.report(**changed))["event_id"])
        refused = [{"subject": "Synthetic"}, {"snippet": "x"}, {"uid": 0}, {"uid": True},
                   {"uid": 2 ** 32}, {"uid": "7"}, {"folder": ""}, {"folder": "IN\nBOX"},
                   {"mailbox": "not-an-address"}]
        for bad in refused:
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    engine_push.new_mail_event(self.report(**bad))
        with self.assertRaises(ValidationError):
            engine_push.new_mail_event({"mailbox": "alice@acme.test", "folder": "INBOX"})
        with self.assertRaises(ValidationError):
            engine_push.mailbox_event(self.report(event="not-a-real-event"))

        # The same message may change state repeatedly, so mailbox-change ids
        # must not be de-duplicated by message identity.
        first = engine_push.mailbox_event(self.report(event="mailbox_changed"))
        second = engine_push.mailbox_event(self.report(event="mailbox_changed"))
        self.assertEqual("mailbox_changed", first["event"])
        self.assertNotEqual(first["event_id"], second["event_id"])

    def test_the_relay_never_blocks_retries_only_silence_and_forwards_only_the_event(self):
        event = engine_push.new_mail_event(self.report())
        self.assertFalse(engine_push.Relay(url="", secret="s", autostart=False).submit(event))

        queued = engine_push.Relay(url="http://backend:8000/x/", secret="s", maxsize=2,
                                   autostart=False)
        self.assertEqual([True, True, False], [queued.submit(event) for _ in range(3)])

        def relay(*answers):
            sent, slept = [], []
            answers = list(answers)
            r = engine_push.Relay(url="http://backend:8000/x/", secret="s", autostart=False,
                                  send=lambda e: (sent.append(e), answers.pop(0))[1],
                                  sleep=slept.append)
            return r, sent, slept

        r, sent, slept = relay(0, 503, 202)
        self.assertEqual("delivered", r.deliver(event))
        self.assertEqual(([event] * 3, [2, 5]), (sent, slept))
        r, sent, _ = relay(403)
        self.assertEqual(("refused", 1), (r.deliver(event), len(sent)))
        r, sent, _ = relay(500, 500, 500)
        self.assertEqual(("unreachable", 3), (r.deliver(event), len(sent)))

        # The real request: its own header, the event and nothing else, no
        # redirects and no proxy.
        captured = {}

        class Answer:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(request, timeout):
            captured.update(request=request, timeout=timeout)
            return Answer()

        with mock.patch.object(engine_push._OPENER, "open", side_effect=fake_open):
            status = engine_push.Relay(url="http://backend:8000/api/internal/postbox/push-events/",
                                       secret="test-only-relay", autostart=False)._post(event)
        request = captured["request"]
        self.assertEqual((202, "POST", 3), (status, request.get_method(), captured["timeout"]))
        self.assertEqual("test-only-relay", request.get_header("X-postbox-push-secret"))
        self.assertEqual(event, json.loads(request.data))
        self.assertIsNone(engine_push._NoRedirect().redirect_request(None, None, 302, "", {}, ""))
        # Passing an empty ProxyHandler keeps urllib from installing its
        # default, environment-reading one, so no proxy can ever be in the path.
        self.assertEqual([], [h for h in engine_push._OPENER.handlers
                              if isinstance(h, urllib.request.ProxyHandler) and h.proxies])
        self.assertTrue(any(isinstance(h, engine_push._NoRedirect)
                            for h in engine_push._OPENER.handlers))
