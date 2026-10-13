"""Phase 3 inactive platform staging: security, audit, independent secrets."""
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox
from apps.platform_admin.models import PlatformAuditLog
from apps.platform_admin.system_mail import DEFAULT_PROPOSAL
from tests.factories import FAST_PASSWORD_HASHERS, auth_client, disable_throttling, make_tenant, make_user


STAGE = "/api/platform/system-mail/stage/"
CONFIG = "/api/platform/system-mail/configuration/"
ALLOWED = {
    "SYSTEM_MAIL_STAGING_ENABLED": True,
    "NATIVE_ENGINE_API_URL": "http://native-api:8451",
    "NATIVE_ENGINE_API_SECRET": "ordinary-secret-only-here",
    "NATIVE_ENGINE_PLATFORM_SECRET": "dedicated-platform-secret-at-least-32-chars",
}


def fake_native_result():
    return {
        "domain": "system.matemail.pro", "state": "inactive",
        "roles": 5, "dkim_modified": False,
        "passwords_created": False, "delivery_activated": False,
    }


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class PlatformSystemMailStagingTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("stage-admin@platform.test", is_platform_admin=True)
        self.tenant_user = make_user("stage-tenant@platform.test")
        self.tenant = make_tenant(self.tenant_user, name="Stage Tenant", slug="stage-tenant")
        self.client = auth_client(self.admin)
        self.tenant_client = auth_client(self.tenant_user, self.tenant)
        self.payload = {"expected_revision": 1, "confirmation": "stage_inactive"}

    def create_draft(self):
        return self.client.post(CONFIG, {**DEFAULT_PROPOSAL, "expected_revision": 0}, format="json")

    @override_settings(**ALLOWED)
    def test_anonymous_and_tenant_never_stage(self):
        self.assertEqual(self.create_draft().status_code, 201)
        with mock.patch("apps.platform_admin.system_mail_stage.requests.post") as post:
            for client in (self.tenant_client, APIClient()):
                self.assertIn(client.post(STAGE, self.payload, format="json").status_code, (401, 403))
            post.assert_not_called()

    def test_default_off_is_a_hard_gate_with_no_audit_or_engine_request(self):
        self.assertEqual(self.create_draft().status_code, 201)
        with mock.patch("apps.platform_admin.system_mail_stage.requests.post") as post:
            response = self.client.post(STAGE, self.payload, format="json")
            self.assertEqual(response.status_code, 503)
            post.assert_not_called()
        self.assertFalse(PlatformAuditLog.objects.filter(action__startswith="system_mail.stage").exists())

    @override_settings(**ALLOWED)
    def test_revision_required_and_stale_revision_rejected(self):
        with mock.patch("apps.platform_admin.system_mail_stage.requests.post") as post:
            self.assertEqual(self.client.post(STAGE, self.payload, format="json").status_code, 409)
            self.assertEqual(self.create_draft().status_code, 201)
            self.assertEqual(self.client.post(STAGE, {**self.payload, "expected_revision": 2}, format="json").status_code, 409)
            post.assert_not_called()

    @override_settings(**ALLOWED)
    def test_field_allowlist_blocks_activation_password_and_other_domain(self):
        self.assertEqual(self.create_draft().status_code, 201)
        with mock.patch("apps.platform_admin.system_mail_stage.requests.post") as post:
            for payload in (
                {**self.payload, "active": True},
                {**self.payload, "password": "forbidden"},
                {**self.payload, "domain": "another.example"},
                {**self.payload, "confirmation": "activate_now"},
                [],
            ):
                with self.subTest(payload=payload):
                    self.assertEqual(self.client.post(STAGE, payload, format="json").status_code, 400)
            post.assert_not_called()

    @override_settings(**ALLOWED)
    def test_valid_request_uses_dedicated_secret_only_and_audits_result(self):
        self.assertEqual(self.create_draft().status_code, 201)
        response = mock.Mock(status_code=200)
        response.json.return_value = fake_native_result()
        with mock.patch("apps.platform_admin.system_mail_stage.requests.post", return_value=response) as post:
            output = self.client.post(STAGE, self.payload, format="json")
        self.assertEqual(output.status_code, 200, output.data)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["X-Native-Platform-Secret"], ALLOWED["NATIVE_ENGINE_PLATFORM_SECRET"])
        self.assertNotIn("X-Native-Api-Secret", kwargs["headers"])
        self.assertEqual(kwargs["json"], {"domain": "system.matemail.pro", "operation": "stage_inactive"})
        self.assertEqual(output.data["state"], "inactive")
        self.assertFalse(output.data["delivery_activated"])
        self.assertFalse(output.data["activation_supported"])
        self.assertEqual(
            list(PlatformAuditLog.objects.filter(action__startswith="system_mail.stage")
                 .order_by("created_at").values_list("action", flat=True)),
            ["system_mail.stage_requested", "system_mail.stage_confirmed"],
        )
        self.assertEqual(Domain.objects.count(), 0)
        self.assertEqual(Mailbox.objects.count(), 0)

    @override_settings(**ALLOWED)
    def test_response_claiming_activation_is_unconfirmed_and_audited(self):
        self.assertEqual(self.create_draft().status_code, 201)
        reply = mock.Mock(status_code=200)
        reply.json.return_value = {**fake_native_result(), "delivery_activated": True}
        with mock.patch("apps.platform_admin.system_mail_stage.requests.post", return_value=reply):
            output = self.client.post(STAGE, self.payload, format="json")
        self.assertEqual(output.status_code, 503)
        self.assertIn("unconfirmed", output.data["detail"])
        self.assertEqual(
            PlatformAuditLog.objects.filter(action="system_mail.stage_outcome_unknown").count(), 1,
        )

    @override_settings(**ALLOWED)
    def test_timeout_does_not_claim_no_mutation(self):
        import requests
        self.assertEqual(self.create_draft().status_code, 201)
        with mock.patch("apps.platform_admin.system_mail_stage.requests.post", side_effect=requests.Timeout("unsafe")):
            output = self.client.post(STAGE, self.payload, format="json")
        self.assertEqual(output.status_code, 503)
        self.assertNotIn("unsafe", str(output.data))
        self.assertEqual(PlatformAuditLog.objects.filter(action="system_mail.stage_requested").count(), 1)
        self.assertEqual(PlatformAuditLog.objects.filter(action="system_mail.stage_outcome_unknown").count(), 1)

    @override_settings(**{**ALLOWED, "NATIVE_ENGINE_PLATFORM_SECRET": "ordinary-secret-only-here"})
    def test_same_secret_as_tenant_provisioning_is_refused(self):
        self.assertEqual(self.create_draft().status_code, 201)
        with mock.patch("apps.platform_admin.system_mail_stage.requests.post") as post:
            response = self.client.post(STAGE, self.payload, format="json")
        self.assertEqual(response.status_code, 503)
        post.assert_not_called()
