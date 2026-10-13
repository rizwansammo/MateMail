"""Release 3 system DKIM preparation is disabled by default and audited."""
from unittest import mock

import requests
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.platform_admin.models import PlatformAuditLog
from apps.platform_admin.system_mail import DEFAULT_PROPOSAL
from tests.factories import (
    FAST_PASSWORD_HASHERS, auth_client, disable_throttling, make_tenant, make_user,
)

CONFIG = "/api/platform/system-mail/configuration/"
DKIM = "/api/platform/system-mail/prepare-dkim/"
ENABLED = {
    "SYSTEM_MAIL_STAGING_ENABLED": True,
    "SYSTEM_MAIL_DKIM_PREPARE_ENABLED": True,
    "NATIVE_ENGINE_API_URL": "http://native-api:8451",
    "NATIVE_ENGINE_API_SECRET": "ordinary-engine-secret",
    "NATIVE_ENGINE_PLATFORM_SECRET": "separate-platform-secret-at-least-32-chars",
}
OBSERVED = {"mx": "observed", "spf": "observed", "dmarc": "observed"}


def reply():
    response = mock.Mock(status_code=200)
    response.json.return_value = {
        "domain": "system.matemail.pro",
        "state": "inactive",
        "selector": "mm1",
        "dns_record_name": "mm1._domainkey.system.matemail.pro",
        "dns_record_value": "v=DKIM1; k=rsa; p=TESTPUBLIC",
        "mail_delivery_activated": False,
        "credentials_created": False,
    }
    return response


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class SystemDkimPreparationTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("dkim-platform@test.example", is_platform_admin=True)
        self.customer = make_user("dkim-tenant@test.example")
        self.tenant = make_tenant(self.customer, name="DKIM Customer", slug="dkim-test")
        self.client = auth_client(self.admin)
        self.tenant_client = auth_client(self.customer, self.tenant)
        saved = self.client.post(
            CONFIG, {**DEFAULT_PROPOSAL, "expected_revision": 0}, format="json"
        )
        self.assertEqual(saved.status_code, 201, saved.data)
        self.body = {"expected_revision": 1, "confirmation": "prepare_dkim_inactive"}

    def test_default_disabled_no_engine_request(self):
        with mock.patch("apps.platform_admin.system_mail_dkim.requests.post") as engine:
            self.assertEqual(self.client.post(DKIM, self.body, format="json").status_code, 503)
            engine.assert_not_called()

    @override_settings(**ENABLED)
    def test_tenant_and_anonymous_denied_before_engine(self):
        with mock.patch("apps.platform_admin.system_mail_dkim.requests.post") as engine:
            for client in (APIClient(), self.tenant_client):
                self.assertIn(client.post(DKIM, self.body, format="json").status_code, (401, 403))
            engine.assert_not_called()

    @override_settings(**ENABLED)
    def test_valid_call_uses_only_dedicated_secret_and_audits(self):
        with mock.patch("apps.platform_admin.system_mail_dkim.inspect_system_mail_dns", return_value=OBSERVED), \
             mock.patch("apps.platform_admin.system_mail_dkim.requests.post", return_value=reply()) as engine:
            result = self.client.post(DKIM, self.body, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertFalse(result.data["activation_supported"])
        self.assertFalse(result.data["credentials_created"])
        self.assertEqual(result.data["dns_record_name"], "mm1._domainkey.system.matemail.pro")
        self.assertEqual(engine.call_args.kwargs["headers"], {
            "X-Native-Platform-Secret": ENABLED["NATIVE_ENGINE_PLATFORM_SECRET"]
        })
        self.assertEqual(engine.call_args.kwargs["json"], {
            "domain": "system.matemail.pro", "operation": "prepare_dkim_inactive",
        })
        self.assertEqual(
            list(PlatformAuditLog.objects.filter(action__startswith="system_mail.dkim_prepare")
                 .order_by("created_at").values_list("action", flat=True)),
            ["system_mail.dkim_prepare_requested", "system_mail.dkim_prepare_confirmed"],
        )

    @override_settings(**ENABLED)
    def test_dns_missing_fails_closed_without_engine(self):
        with mock.patch("apps.platform_admin.system_mail_dkim.inspect_system_mail_dns",
                        return_value={**OBSERVED, "spf": "missing_or_mismatched"}), \
             mock.patch("apps.platform_admin.system_mail_dkim.requests.post") as engine:
            output = self.client.post(DKIM, self.body, format="json")
        self.assertEqual(output.status_code, 503)
        engine.assert_not_called()

    @override_settings(**ENABLED)
    def test_rejects_arbitrary_or_activation_fields(self):
        with mock.patch("apps.platform_admin.system_mail_dkim.requests.post") as engine:
            for value in ({**self.body, "activate": True},
                          {**self.body, "domain": "mail.matemail.pro"},
                          {**self.body, "password": "forbidden"},
                          {**self.body, "confirmation": "rotate_dkim"}):
                self.assertEqual(self.client.post(DKIM, value, format="json").status_code, 400)
            engine.assert_not_called()

    @override_settings(**ENABLED)
    def test_engine_timeout_records_unknown_without_false_failure_claim(self):
        with mock.patch("apps.platform_admin.system_mail_dkim.inspect_system_mail_dns", return_value=OBSERVED), \
             mock.patch("apps.platform_admin.system_mail_dkim.requests.post",
                        side_effect=requests.Timeout("sensitive diagnostic")):
            output = self.client.post(DKIM, self.body, format="json")
        self.assertEqual(output.status_code, 503)
        self.assertNotIn("sensitive diagnostic", str(output.data))
        self.assertEqual(
            PlatformAuditLog.objects.filter(action="system_mail.dkim_prepare_outcome_unknown").count(), 1
        )
