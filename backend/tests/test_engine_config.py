"""Native Mail Engine deployment configuration guards."""
from django.core.checks import Error
from django.test import SimpleTestCase, override_settings

from apps.mail_engine.checks import (
    mail_engine_adapter_is_known,
    native_engine_configured,
)

NATIVE_URL = "http://matemail-native-api:8451"
NATIVE_SECRET = "not-a-real-secret"


def _ids(results):
    return sorted(r.id for r in results)


class StubAdapterNeedsNoConfigurationTest(SimpleTestCase):
    @override_settings(
        MAIL_ENGINE_ADAPTER="stub",
        NATIVE_ENGINE_API_URL="",
        NATIVE_ENGINE_API_SECRET="",
    )
    def test_stub_needs_no_native_configuration(self):
        self.assertEqual(native_engine_configured(None), [])


class NativeEngineConfigurationTest(SimpleTestCase):
    @override_settings(
        MAIL_ENGINE_ADAPTER="native",
        NATIVE_ENGINE_API_URL=NATIVE_URL,
        NATIVE_ENGINE_API_SECRET=NATIVE_SECRET,
    )
    def test_complete_native_configuration_passes(self):
        self.assertEqual(native_engine_configured(None), [])

    @override_settings(
        MAIL_ENGINE_ADAPTER="native",
        NATIVE_ENGINE_API_URL="",
        NATIVE_ENGINE_API_SECRET=NATIVE_SECRET,
    )
    def test_missing_url_fails(self):
        self.assertIn("mail_engine.E011", _ids(native_engine_configured(None)))

    @override_settings(
        MAIL_ENGINE_ADAPTER="native",
        NATIVE_ENGINE_API_URL=NATIVE_URL,
        NATIVE_ENGINE_API_SECRET="",
    )
    def test_missing_secret_fails(self):
        self.assertIn("mail_engine.E012", _ids(native_engine_configured(None)))

    @override_settings(
        MAIL_ENGINE_ADAPTER="native",
        NATIVE_ENGINE_API_URL="http://127.0.0.1:8451",
        NATIVE_ENGINE_API_SECRET=NATIVE_SECRET,
    )
    def test_loopback_url_fails(self):
        self.assertIn("mail_engine.E013", _ids(native_engine_configured(None)))

    @override_settings(
        MAIL_ENGINE_ADAPTER="native",
        NATIVE_ENGINE_API_URL="ftp://matemail-native-api:8451",
        NATIVE_ENGINE_API_SECRET=NATIVE_SECRET,
    )
    def test_invalid_scheme_fails(self):
        self.assertIn("mail_engine.E014", _ids(native_engine_configured(None)))


class AdapterNameTest(SimpleTestCase):
    @override_settings(MAIL_ENGINE_ADAPTER="native")
    def test_native_is_known(self):
        self.assertEqual(mail_engine_adapter_is_known(None), [])

    @override_settings(MAIL_ENGINE_ADAPTER="stub")
    def test_stub_is_known(self):
        self.assertEqual(mail_engine_adapter_is_known(None), [])

    @override_settings(MAIL_ENGINE_ADAPTER="mailcow")
    def test_retired_mailcow_name_is_rejected(self):
        results = mail_engine_adapter_is_known(None)
        self.assertIn("mail_engine.E010", _ids(results))
        self.assertTrue(all(isinstance(r, Error) for r in results))


class ChecksAreRegisteredForDeployTest(SimpleTestCase):
    def test_native_check_is_in_deploy_registry(self):
        from django.core.checks.registry import registry
        names = {c.__name__ for c in registry.get_checks(include_deployment_checks=True)}
        self.assertIn("native_engine_configured", names)
        self.assertIn("mail_engine_adapter_is_known", names)

    def test_native_check_is_not_a_normal_check(self):
        from django.core.checks.registry import registry
        names = {c.__name__ for c in registry.get_checks(include_deployment_checks=False)}
        self.assertNotIn("native_engine_configured", names)
