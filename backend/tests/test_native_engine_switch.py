"""
Switching the MateMail control plane to the Native Engine (NE5).

WHAT THIS COVERS
    The seam where the application chooses an engine, and the guarantees that
    have to hold once it chooses the Native one: that the choice is explicit,
    that a misconfigured choice cannot deploy, that nothing silently falls back,
    and that switching the control plane does not touch how transactional mail
    is actually sent.

WHAT NE5 IS NOT
    It is not a mail migration. `MAIL_ENGINE_ADAPTER` selects who provisions
    domains and mailboxes; Django's `EMAIL_*` settings decide how a password
    reset leaves the building. Those are independent, and several tests here
    exist only to keep them that way until NE6 says otherwise.
"""
from __future__ import annotations

import pathlib
import unittest
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from apps.mail_engine import checks as engine_checks
from apps.mail_engine.factory import get_adapter, reset_adapter

REPO = pathlib.Path(__file__).resolve().parents[2]


def _code_only(path: pathlib.Path) -> str:
    """
    A module's executable code, with comments and docstrings removed.

    Several modules explain in prose WHY a rule used to live inside
    `MailcowAdapter` and was moved above it. That history is worth keeping, and
    a grep that cannot tell a comment from an import would force it to be
    deleted to keep the test green.
    """
    import tokenize

    pieces = []
    try:
        with open(path, "rb") as handle:
            tokens = list(tokenize.tokenize(handle.readline))
    except Exception:                            # pragma: no cover
        return path.read_text(encoding="utf-8", errors="ignore")

    previous = None
    for token in tokens:
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
            None, tokenize.ENCODING, tokenize.INDENT,
            tokenize.NEWLINE, tokenize.NL,
        ):
            # A docstring, not a value. ENCODING matters: it is the very first
            # token tokenize emits, so without it the MODULE docstring — the
            # one that usually carries the history — is never stripped.
            continue
        pieces.append(token.string)
        if token.type not in (tokenize.NL, tokenize.NEWLINE):
            previous = token.type
    return " ".join(pieces)

NATIVE_URL = "http://matemail-native-api:8451"
NATIVE_SECRET = "ne5-test-secret-not-a-real-credential"


class AdapterSelectionTest(SimpleTestCase):
    """Which engine the application talks to is a deliberate, explicit choice."""

    def setUp(self):
        reset_adapter()
        self.addCleanup(reset_adapter)

    @override_settings(MAIL_ENGINE_ADAPTER="native",
                       NATIVE_ENGINE_API_URL=NATIVE_URL,
                       NATIVE_ENGINE_API_SECRET=NATIVE_SECRET)
    def test_native_is_selectable(self):
        from apps.mail_engine.native_adapter import NativeMailEngineAdapter
        self.assertIsInstance(get_adapter(), NativeMailEngineAdapter)

    @override_settings(MAIL_ENGINE_ADAPTER="mailcow",
                       MAIL_ENGINE_API_URL="https://mx.example.invalid:8453",
                       MAIL_ENGINE_API_KEY="k")
    def test_mailcow_remains_selectable_as_the_rollback_path(self):
        """
        NE5 switches the control plane; it does not delete the way back. Rolling
        back must stay a configuration change, not a code change.
        """
        from apps.mail_engine.mailcow_adapter import MailcowAdapter
        self.assertIsInstance(get_adapter(), MailcowAdapter)

    @override_settings(MAIL_ENGINE_ADAPTER="stub")
    def test_stub_remains_the_default_for_development(self):
        from apps.mail_engine.stub_adapter import StubAdapter
        self.assertIsInstance(get_adapter(), StubAdapter)

    @override_settings(MAIL_ENGINE_ADAPTER="natve")
    def test_an_unrecognised_name_refuses_rather_than_falling_back(self):
        """
        THE FAILURE MODE THIS PREVENTS. The stub accepts every operation and
        reports success without an engine, so falling back to it on a typo would
        produce a deployment that provisions nothing and says everything worked
        — customers seeing mailboxes that do not exist.
        """
        with self.assertRaises(ImproperlyConfigured):
            get_adapter()

    @override_settings(MAIL_ENGINE_ADAPTER="NATIVE  ",
                       NATIVE_ENGINE_API_URL=NATIVE_URL,
                       NATIVE_ENGINE_API_SECRET=NATIVE_SECRET)
    def test_the_name_is_normalised(self):
        """Case and stray whitespace in an env var must not change the engine."""
        from apps.mail_engine.native_adapter import NativeMailEngineAdapter
        self.assertIsInstance(get_adapter(), NativeMailEngineAdapter)

    @override_settings(MAIL_ENGINE_ADAPTER="native",
                       NATIVE_ENGINE_API_URL=NATIVE_URL,
                       NATIVE_ENGINE_API_SECRET=NATIVE_SECRET)
    def test_the_native_adapter_is_configured_from_settings(self):
        """Nothing hardcodes a host, an address or a credential."""
        adapter = get_adapter()
        self.assertEqual(NATIVE_URL, adapter._base)
        self.assertEqual(
            NATIVE_SECRET, adapter._session.headers["X-Native-Api-Secret"])

    def test_no_container_name_or_address_is_hardcoded_in_the_adapter(self):
        source = (REPO / "backend" / "apps" / "mail_engine"
                  / "native_adapter.py").read_text(encoding="utf-8")
        for literal in ("169.58.114.252", "172.27.0.", "http://matemail-native-api:8451"):
            with self.subTest(literal=literal):
                self.assertNotIn(literal, source)


class NativeDeploymentChecksTest(SimpleTestCase):
    """
    A half-configured switch must fail in the pipeline, not on a customer's
    first action with a domain already half-provisioned.
    """

    def ids(self, **settings_kwargs):
        with override_settings(**settings_kwargs):
            return {m.id for m in engine_checks.native_engine_configured(None)} | {
                m.id for m in engine_checks.mail_engine_adapter_is_known(None)}

    def test_a_complete_native_configuration_passes(self):
        self.assertEqual(set(), self.ids(
            MAIL_ENGINE_ADAPTER="native",
            NATIVE_ENGINE_API_URL=NATIVE_URL,
            NATIVE_ENGINE_API_SECRET=NATIVE_SECRET))

    def test_a_missing_url_is_refused(self):
        self.assertIn("mail_engine.E011", self.ids(
            MAIL_ENGINE_ADAPTER="native", NATIVE_ENGINE_API_URL="",
            NATIVE_ENGINE_API_SECRET=NATIVE_SECRET))

    def test_a_missing_secret_is_refused(self):
        self.assertIn("mail_engine.E012", self.ids(
            MAIL_ENGINE_ADAPTER="native", NATIVE_ENGINE_API_URL=NATIVE_URL,
            NATIVE_ENGINE_API_SECRET=""))

    def test_a_loopback_url_is_refused(self):
        """
        Loopback is this container, never the engine — the same mistake
        EMAIL_HOST=localhost was.
        """
        # IPv6 needs brackets in a URL; without them urlparse reads the host
        # as "" and the check has nothing to reject.
        for host in ("127.0.0.1", "localhost", "[::1]", "0.0.0.0"):
            with self.subTest(host=host):
                self.assertIn("mail_engine.E013", self.ids(
                    MAIL_ENGINE_ADAPTER="native",
                    NATIVE_ENGINE_API_URL=f"http://{host}:8451",
                    NATIVE_ENGINE_API_SECRET=NATIVE_SECRET))

    def test_an_unknown_adapter_name_is_refused(self):
        self.assertIn("mail_engine.E010",
                      self.ids(MAIL_ENGINE_ADAPTER="pigeon"))

    def test_the_native_checks_stay_quiet_for_other_adapters(self):
        """A mailcow or stub deployment must not be failed by Native's rules."""
        for name in ("mailcow", "stub"):
            with self.subTest(adapter=name):
                with override_settings(MAIL_ENGINE_ADAPTER=name):
                    self.assertEqual(
                        [], engine_checks.native_engine_configured(None))

    def test_native_does_not_require_https(self):
        """
        The Native API is on an internal network with no published ports and no
        certificate until NE0.9. Demanding HTTPS would only push someone toward
        a self-signed certificate nobody verifies.
        """
        self.assertEqual(set(), self.ids(
            MAIL_ENGINE_ADAPTER="native",
            NATIVE_ENGINE_API_URL="http://matemail-native-api:8451",
            NATIVE_ENGINE_API_SECRET=NATIVE_SECRET))


class ControlPlaneDoesNotTouchTransactionalMailTest(SimpleTestCase):
    """
    NE5 changes who provisions mailboxes. It must not change how a password
    reset is delivered — that is NE6's, and conflating them would move real
    customer-facing mail during a control-plane change.
    """

    def test_the_settings_are_independent(self):
        base = (REPO / "backend" / "config" / "settings" / "base.py").read_text(encoding="utf-8")
        prod = (REPO / "backend" / "config" / "settings" / "prod.py").read_text(encoding="utf-8")
        for body in (base, prod):
            # No EMAIL_* value may be derived from the adapter choice.
            for line in body.splitlines():
                if line.startswith("EMAIL_") and "MAIL_ENGINE" in line:
                    self.fail(f"transactional SMTP depends on the adapter: {line}")

    def test_no_adapter_sends_transactional_mail(self):
        """
        The adapter contract has no 'send a message' method, and it must not
        grow one by accident — that is the boundary keeping the two concerns
        apart.
        """
        from apps.mail_engine.adapter import MailEngineAdapter
        for name in MailEngineAdapter.__abstractmethods__:
            with self.subTest(method=name):
                self.assertNotIn("send_mail", name)
                self.assertNotIn("send_message", name)

    @override_settings(MAIL_ENGINE_ADAPTER="native",
                       NATIVE_ENGINE_API_URL=NATIVE_URL,
                       NATIVE_ENGINE_API_SECRET=NATIVE_SECRET,
                       EMAIL_HOST="mx.matemail.online", EMAIL_PORT=587)
    def test_selecting_native_leaves_email_settings_alone(self):
        from django.conf import settings
        reset_adapter()
        self.addCleanup(reset_adapter)
        get_adapter()
        self.assertEqual("mx.matemail.online", settings.EMAIL_HOST)
        self.assertEqual(587, settings.EMAIL_PORT)


class NoMailcowLeakageAboveTheAdapterTest(SimpleTestCase):
    """
    Mailcow's vocabulary belongs inside MailcowAdapter. Business logic that knew
    about `rl_frame` or a mailcow object id would break the moment the engine
    changed — which is exactly what NE5 does.
    """

    #: The adapter, its own tests and the factory/checks are allowed to name it.
    ALLOWED = {
        "mailcow_adapter.py", "factory.py", "checks.py",
        "adapter.py", "dto.py", "native_adapter.py", "stub_adapter.py",
    }

    def test_no_application_module_imports_a_mailcow_client(self):
        apps_dir = REPO / "backend" / "apps"
        offenders = []
        for module in apps_dir.rglob("*.py"):
            if module.name in self.ALLOWED:
                continue
            body = _code_only(module)
            for marker in ("MailcowAdapter", "mailcow_adapter", "/api/v1/"):
                if marker in body:
                    offenders.append(f"{module.relative_to(REPO)}: {marker}")
        self.assertEqual([], offenders,
                         "mailcow specifics leaked above the adapter")

    def test_every_engine_call_site_goes_through_the_factory(self):
        """
        A module constructing an adapter directly would pin itself to one engine
        and ignore MAIL_ENGINE_ADAPTER entirely.
        """
        apps_dir = REPO / "backend" / "apps"
        offenders = []
        for module in apps_dir.rglob("*.py"):
            if module.parent.name == "mail_engine":
                continue
            body = module.read_text(encoding="utf-8", errors="ignore")
            if "MailcowAdapter(" in body or "NativeMailEngineAdapter(" in body:
                offenders.append(str(module.relative_to(REPO)))
        self.assertEqual([], offenders)

    def test_the_application_never_reads_mailcow_settings(self):
        apps_dir = REPO / "backend" / "apps"
        offenders = []
        for module in apps_dir.rglob("*.py"):
            if module.name in self.ALLOWED:
                continue
            body = module.read_text(encoding="utf-8", errors="ignore")
            if "MAIL_ENGINE_API_KEY" in body or "MAIL_ENGINE_API_URL" in body:
                offenders.append(str(module.relative_to(REPO)))
        self.assertEqual([], offenders)


class CeleryUsesTheSameAdapterTest(SimpleTestCase):
    """
    Workers and the web process must agree on the engine. A worker still holding
    mailcow while the web tier provisions Native would split the control plane
    in half, and the symptom — resources existing in one engine only — would
    look like random provisioning failures.
    """

    def test_tasks_resolve_the_adapter_through_the_factory(self):
        source = (REPO / "backend" / "apps" / "mail_engine"
                  / "tasks.py").read_text(encoding="utf-8")
        self.assertIn("from .factory import get_adapter", source)
        self.assertNotIn("MailcowAdapter(", source)
        self.assertNotIn("NativeMailEngineAdapter(", source)

    def test_tasks_resolve_it_per_call_rather_than_at_import(self):
        """
        Resolving inside the task means a worker restarted after a settings
        change picks up the new engine, and that tests can switch engines
        without reloading the module.
        """
        source = (REPO / "backend" / "apps" / "mail_engine"
                  / "tasks.py").read_text(encoding="utf-8")
        head = source[:source.index("def ")] if "def " in source else source
        self.assertNotIn("get_adapter()", head,
                         "the adapter must not be resolved at import time")

    @override_settings(MAIL_ENGINE_ADAPTER="native",
                       NATIVE_ENGINE_API_URL=NATIVE_URL,
                       NATIVE_ENGINE_API_SECRET=NATIVE_SECRET)
    def test_a_task_body_gets_the_native_adapter(self):
        from apps.mail_engine.native_adapter import NativeMailEngineAdapter
        reset_adapter()
        self.addCleanup(reset_adapter)
        self.assertIsInstance(get_adapter(), NativeMailEngineAdapter)


class NativeOutageDoesNotFallBackTest(SimpleTestCase):
    """
    An unreachable engine must surface as a retryable failure, never as a quiet
    switch to the other engine. Falling back would provision a customer into
    mailcow while MateMail believed it was using Native, and the two would
    diverge silently.
    """

    def setUp(self):
        reset_adapter()
        self.addCleanup(reset_adapter)

    @override_settings(MAIL_ENGINE_ADAPTER="native",
                       NATIVE_ENGINE_API_URL=NATIVE_URL,
                       NATIVE_ENGINE_API_SECRET=NATIVE_SECRET)
    def test_a_transport_failure_raises_engine_unavailable(self):
        import requests

        from apps.mail_engine.dto import DomainSpec
        from apps.mail_engine.errors import EngineUnavailable

        adapter = get_adapter()
        with mock.patch.object(
            adapter._session, "request",
            side_effect=requests.ConnectionError("engine down"),
        ):
            with self.assertRaises(EngineUnavailable):
                adapter.ensure_domain(DomainSpec(name="ne5.invalid"))

    @override_settings(MAIL_ENGINE_ADAPTER="native",
                       NATIVE_ENGINE_API_URL=NATIVE_URL,
                       NATIVE_ENGINE_API_SECRET=NATIVE_SECRET)
    def test_the_adapter_never_becomes_mailcow_on_failure(self):
        import requests

        from apps.mail_engine.native_adapter import NativeMailEngineAdapter

        from apps.mail_engine.dto import DomainSpec

        adapter = get_adapter()
        with mock.patch.object(
            adapter._session, "request",
            side_effect=requests.ConnectionError("engine down"),
        ):
            # `check_health` deliberately REPORTS rather than raises, so a
            # mutating call is used here: the question is whether a failure can
            # quietly become a mailcow call, and only a mutation could.
            with self.assertRaises(Exception):
                adapter.ensure_domain(DomainSpec(name="ne5-outage.invalid"))
        # Still Native after the failure: nothing swapped the singleton.
        self.assertIsInstance(get_adapter(), NativeMailEngineAdapter)

    def test_engine_unavailable_is_classified_as_retryable(self):
        """
        Durable jobs depend on this: a temporary outage must produce an error a
        retry can clear, not a permanent one that drops the work.
        """
        from apps.mail_engine.errors import EngineUnavailable, MailEngineError
        self.assertTrue(issubclass(EngineUnavailable, MailEngineError))


if __name__ == "__main__":                       # pragma: no cover
    unittest.main()
