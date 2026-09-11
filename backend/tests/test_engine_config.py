"""
The Mail Engine connection must be impossible to misconfigure into production.

The stub adapter needs no configuration, which is what lets CI and local
development run without an engine — and is also how a real-engine deployment
could ship with no URL or no API key and only discover it when a customer's
first domain half-provisions. These checks close that gap, and these tests keep
them honest in both directions: strict for the real engine, silent for the stub.
"""
from django.core.checks import Error, Warning
from django.test import SimpleTestCase, override_settings

from apps.mail_engine.checks import mail_engine_configured

GOOD_URL = "https://mx.matemail.online:8453"
GOOD_KEY = "not-a-real-key-for-tests"


def _ids(results):
    return sorted(r.id for r in results)


class StubAdapterNeedsNoConfigurationTest(SimpleTestCase):
    """
    CI runs on the stub and supplies neither URL nor key. If these checks ever
    fire for the stub, every CI job breaks — so assert the silence explicitly
    rather than relying on nobody noticing.
    """

    @override_settings(
        MAIL_ENGINE_ADAPTER="stub", MAIL_ENGINE_API_URL="", MAIL_ENGINE_API_KEY=""
    )
    def test_no_complaint_without_a_url_or_key(self):
        self.assertEqual(mail_engine_configured(None), [])

    @override_settings(
        MAIL_ENGINE_ADAPTER="stub",
        MAIL_ENGINE_API_URL="http://localhost:8080",
        MAIL_ENGINE_API_KEY="",
    )
    def test_no_complaint_about_a_plain_http_loopback_url(self):
        self.assertEqual(mail_engine_configured(None), [])


class RealEngineRequiresFullConfigurationTest(SimpleTestCase):
    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL=GOOD_URL,
        MAIL_ENGINE_API_KEY=GOOD_KEY,
    )
    def test_a_complete_configuration_passes(self):
        self.assertEqual(mail_engine_configured(None), [])

    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL="",
        MAIL_ENGINE_API_KEY=GOOD_KEY,
    )
    def test_a_missing_url_is_an_error(self):
        self.assertIn("mail_engine.E001", _ids(mail_engine_configured(None)))

    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL=GOOD_URL,
        MAIL_ENGINE_API_KEY="",
    )
    def test_a_missing_api_key_is_an_error(self):
        self.assertIn("mail_engine.E002", _ids(mail_engine_configured(None)))

    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL=GOOD_URL,
        MAIL_ENGINE_API_KEY="   ",
    )
    def test_a_whitespace_api_key_counts_as_missing(self):
        self.assertIn("mail_engine.E002", _ids(mail_engine_configured(None)))

    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL="http://mx.matemail.online:8025",
        MAIL_ENGINE_API_KEY=GOOD_KEY,
    )
    def test_plain_http_is_an_error(self):
        """
        The API key travels as a header on every call. Plain HTTP puts it in
        clear on a Docker bridge that every container on the host can reach.
        """
        self.assertIn("mail_engine.E003", _ids(mail_engine_configured(None)))

    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL="https://127.0.0.1:8453",
        MAIL_ENGINE_API_KEY=GOOD_KEY,
    )
    def test_a_loopback_host_is_flagged(self):
        """
        Loopback is this container, never the engine. Same class of mistake as
        the EMAIL_HOST=localhost default that silently discarded every
        transactional message.
        """
        results = mail_engine_configured(None)
        self.assertIn("mail_engine.W001", _ids(results))
        self.assertTrue(any(isinstance(r, Warning) for r in results))

    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL="",
        MAIL_ENGINE_API_KEY="",
    )
    def test_everything_missing_reports_everything(self):
        self.assertEqual(
            _ids(mail_engine_configured(None)), ["mail_engine.E001", "mail_engine.E002"]
        )

    @override_settings(
        MAIL_ENGINE_ADAPTER="MAILCOW",
        MAIL_ENGINE_API_URL="",
        MAIL_ENGINE_API_KEY="",
    )
    def test_the_adapter_name_is_matched_case_insensitively(self):
        self.assertNotEqual(mail_engine_configured(None), [])


class ChecksNeverRevealCredentialsTest(SimpleTestCase):
    SECRET = "SUPERSECRETENGINEKEY12345"

    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL="http://mx.matemail.online:8025",
        MAIL_ENGINE_API_KEY=SECRET,
    )
    def test_no_check_message_contains_the_api_key(self):
        blob = " ".join(
            f"{r.msg} {r.hint}" for r in mail_engine_configured(None)
        )
        self.assertNotIn(self.SECRET, blob)

    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL="",
        MAIL_ENGINE_API_KEY=SECRET,
    )
    def test_the_key_is_not_echoed_when_reporting_a_missing_url(self):
        blob = " ".join(
            f"{r.msg} {r.hint}" for r in mail_engine_configured(None)
        )
        self.assertNotIn(self.SECRET, blob)


class ChecksAreRegisteredForDeployTest(SimpleTestCase):
    """
    A check nothing runs is documentation. `manage.py check --deploy` gates the
    pipeline, so the check has to be registered with deploy=True to matter.
    """

    def test_the_check_is_in_the_deploy_registry(self):
        from django.core.checks.registry import registry

        deploy_checks = {c.__name__ for c in registry.get_checks(include_deployment_checks=True)}
        self.assertIn("mail_engine_configured", deploy_checks)

    def test_it_is_not_run_outside_deploy_checks(self):
        from django.core.checks.registry import registry

        normal = {c.__name__ for c in registry.get_checks(include_deployment_checks=False)}
        self.assertNotIn("mail_engine_configured", normal)

    @override_settings(
        MAIL_ENGINE_ADAPTER="mailcow",
        MAIL_ENGINE_API_URL="",
        MAIL_ENGINE_API_KEY="",
    )
    def test_manage_py_check_deploy_fails_on_an_incomplete_configuration(self):
        from django.core.management import call_command
        from django.core.management.base import SystemCheckError

        with self.assertRaises(SystemCheckError):
            call_command("check", "--deploy", "--fail-level", "ERROR")

    def test_errors_are_errors_not_warnings(self):
        """Severity is what makes --deploy fail. A Warning would not gate."""
        with override_settings(
            MAIL_ENGINE_ADAPTER="mailcow",
            MAIL_ENGINE_API_URL="",
            MAIL_ENGINE_API_KEY="",
        ):
            results = mail_engine_configured(None)
        self.assertTrue(results)
        self.assertTrue(all(isinstance(r, Error) for r in results))
