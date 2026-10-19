"""Phase 7C: test truthful partial-commit status across Native -> PostBox.

Never invoke a real engine, mailbox, user secret, or network service. The
HTTP responses are carefully simulated, while the actual adapter and Django
PostBox request/exception handling are exercised.
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.mail_engine.errors import (
    EngineSessionDisconnectUnverified, EngineUnavailable,
)
from apps.mail_engine.native_adapter import NativeMailEngineAdapter
from apps.postbox import auth as postbox_auth
from apps.postbox.models import PostBoxSession
from tests.factories import FAST_PASSWORD_HASHERS, make_domain, make_mailbox, make_tenant, make_user


def synthetic_response(status, body):
    response = mock.Mock()
    response.status_code = status
    response.json.return_value = body
    response.content = b"synthetic"
    return response


class AdapterPartialCommitContract(SimpleTestCase):
    def setUp(self):
        self.adapter = NativeMailEngineAdapter(
            base_url="http://native-api.synthetic.invalid:8451",
            secret="synthetic-test-only",
        )

    def test_exact_coded_revoke_503_preserves_committed_status(self):
        with mock.patch.object(self.adapter._session, "request", return_value=synthetic_response(
            503, {"code": "mail_client_disconnect_unverified", "error": "unverified"},
        )):
            with self.assertRaises(EngineSessionDisconnectUnverified):
                self.adapter.revoke_client_credential(
                    "alice@synthetic.invalid",
                    "11111111-1111-4111-8111-111111111111",
                )

    def test_uncoded_revoke_and_unrelated_503_never_claim_commit(self):
        for path, payload in (
            ("/v1/mailboxes/client/revoke", {"error": "service down"}),
            ("/v1/mailboxes/client/issue", {
                "code": "mail_client_disconnect_unverified",
            }),
        ):
            with self.subTest(path=path), mock.patch.object(
                self.adapter._session, "request",
                return_value=synthetic_response(503, payload),
            ):
                with self.assertRaises(EngineUnavailable):
                    self.adapter._request("POST", path, json={})

    def test_server_malformed_503_never_claims_commit(self):
        response = synthetic_response(503, {})
        response.json.side_effect = ValueError("not JSON")
        with mock.patch.object(self.adapter._session, "request", return_value=response):
            with self.assertRaises(EngineUnavailable):
                self.adapter.set_client_policy(
                    "alice@synthetic.invalid", True, False,
                )


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    POSTBOX_2FA_ENABLED=True,
    SMTP_IMAP_SECURITY_ENABLED=True,
    MAIL_ENGINE_ADAPTER="native",
    CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "phase7c-partial-commit",
    }},
)
class PostBoxPartialCommitAPI(TestCase):
    def setUp(self):
        owner = make_user("stage7c-test-owner@synthetic.invalid")
        tenant = make_tenant(owner, "Stage 7C Partial Commit", "stage7c-partial-commit")
        domain = make_domain(tenant, "stage7c.synthetic.invalid")
        self.mailbox = make_mailbox(tenant, domain, "alice")
        self.mailbox.mail_engine_provisioned = True
        self.mailbox.save(update_fields=["mail_engine_provisioned"])
        self.client = APIClient()
        token, _ = PostBoxSession.issue(self.mailbox)
        self.client.cookies[postbox_auth.SESSION_COOKIE_NAME] = token

    def test_postbox_returns_explicit_pending_disconnection(self):
        adapter = mock.Mock()
        adapter.revoke_client_credential.side_effect = EngineSessionDisconnectUnverified(
            "status=503, operation committed",
            operation="revoke_client_credential",
        )
        with mock.patch(
            "apps.postbox.two_factor_views._require_recent_primary_and_factor",
            return_value=True,
        ), mock.patch(
            "apps.postbox.two_factor_views.get_adapter",
            return_value=adapter,
        ):
            result = self.client.post(
                "/api/postbox/security/smtp-imap/",
                {
                    "action": "revoke",
                    "current_password": "synthetic-primary",
                    "credential_id": "11111111-1111-4111-8111-111111111111",
                },
                format="json",
            )
        self.assertEqual(result.status_code, 503, result.data)
        self.assertEqual(result.data["code"], "mail_client_disconnect_unverified")
        self.assertIn("saved", result.data["detail"])
        self.assertIn("Retry", result.data["detail"])
        adapter.revoke_client_credential.assert_called_once()
