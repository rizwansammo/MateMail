"""
No Mail Engine detail may reach a customer.

DEC-011: customers must never learn the engine exists, what it is built from, or
who makes it. These tests attack the paths through which engine detail
previously escaped, and assert on real serialized API responses rather than on
internal state.

The two historical leaks this pins shut:

- **L1** — `apps/mailboxes/views.py` wrote `str(exc)[:500]` into a
  customer-visible field, so a connection failure published the engine's
  hostname and port.
- **L2** — the old term-substitution sanitizer was case-sensitive and
  incomplete: `Mailcow`, `postfix`, `SOGo`, `ClamAV` and engine API paths all
  passed through unchanged.
"""
import re
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.mail_engine.errors import (
    ALL_ERROR_TYPES,
    EngineUnavailable,
    MailEngineError,
    Rejected,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "engine-leak-tests",
    }
}

# Anything here appearing in a customer-visible response is a DEC-011 breach.
FORBIDDEN_TERMS = [
    "mailcow", "postfix", "dovecot", "rspamd", "sogo", "roundcube", "clamav",
    "solr", "unbound", "netfilter", "mariadb", "mysql",
    "/api/v1/", "x-api-key",
    "mail-engine-nginx", "mailcow-nginx", "localhost:8080", ":8080", ":1025",
    "httpconnectionpool", "max retries exceeded", "traceback",
]

# Realistic engine failures, including the exact shapes that used to leak.
ENGINE_FAILURE_MESSAGES = [
    "mailcow API error",
    "Mailcow API error",
    "MAILCOW failure",
    "postfix rejected the recipient",
    "Postfix rejected the recipient",
    "dovecot auth failed",
    "Dovecot auth failed",
    "SOGo unavailable",
    "Roundcube error",
    "ClamAV timeout",
    "MariaDB connection lost",
    "HTTPConnectionPool(host='mailcow-nginx', port=8080): Max retries exceeded "
    "with url: /api/v1/add/mailbox",
    "/api/v1/add/mailbox returned 500",
    "X-API-Key rejected by mailcow-nginx:8080",
]


def assert_clean(testcase, blob: str, *, context: str):
    lowered = blob.lower()
    for term in FORBIDDEN_TERMS:
        message = f"{context} leaked engine detail {term!r}: {blob[:400]}"
        if term.isalnum():
            # Engine product names must appear as vocabulary, not merely as a
            # coincidental byte sequence inside an opaque customer token. A
            # random verification token can legitimately contain e.g. "sogo".
            testcase.assertIsNone(
                re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered),
                message,
            )
        else:
            testcase.assertNotIn(term, lowered, message)


class ErrorTypeLeakTest(TestCase):
    """The exception types themselves must be safe to handle carelessly."""

    def test_opaque_token_substrings_are_not_engine_vocabulary(self):
        assert_clean(
            self,
            '{"verification_record_value":"matemail-verify-abcsogoxyz"}',
            context="opaque verification token",
        )
        with self.assertRaises(AssertionError):
            assert_clean(self, "SOGo unavailable", context="real engine vocabulary")

    def test_customer_message_never_contains_engine_detail(self):
        for error_type in ALL_ERROR_TYPES:
            for detail in ENGINE_FAILURE_MESSAGES:
                with self.subTest(error=error_type.__name__, detail=detail[:30]):
                    exc = error_type(detail, operation="ensure_mailbox")
                    assert_clean(self, exc.customer_message, context=error_type.__name__)

    def test_str_of_exception_is_the_customer_message(self):
        """
        The old L1 anti-pattern was `field = str(exc)`. Even that must be safe,
        so str() is deliberately the customer message, not the detail.
        """
        for error_type in ALL_ERROR_TYPES:
            for detail in ENGINE_FAILURE_MESSAGES:
                with self.subTest(error=error_type.__name__):
                    exc = error_type(detail)
                    self.assertEqual(str(exc), exc.customer_message)
                    assert_clean(self, str(exc), context=f"str({error_type.__name__})")

    def test_technical_detail_is_retained_for_logs(self):
        """Detail must survive for operators — it just must not be customer-facing."""
        exc = EngineUnavailable("HTTPConnectionPool(host='mailcow-nginx', port=8080)")
        self.assertIn("mailcow-nginx", exc.technical_detail)
        self.assertIn("mailcow-nginx", exc.log_message)
        self.assertNotIn("mailcow", str(exc).lower())

    def test_every_error_type_has_its_own_customer_message(self):
        messages = {t.__name__: t.customer_message for t in ALL_ERROR_TYPES}
        self.assertEqual(len(set(messages.values())), len(messages))


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class ApiResponseLeakTest(TestCase):
    """Drive the real API while the engine fails, and inspect what customers get."""

    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("leak@example.test")
        self.tenant = make_tenant(self.user, name="Leak Co", slug="leak-co")
        self.client_api = auth_client(self.user, self.tenant)
        self.domain = make_domain(self.tenant, "leak.example")

    def _create_mailbox(self):
        return self.client_api.post(
            "/api/mailboxes/",
            {
                "local_part": "alice",
                "domain_id": str(self.domain.id),
                "full_name": "Alice",
                "password": "Mailbox-Passphrase-9",
            },
            format="json",
        )

    def test_mailbox_response_is_clean_when_engine_raises_typed_error(self):
        for message in ENGINE_FAILURE_MESSAGES:
            with self.subTest(engine_says=message[:40]):
                from apps.mailboxes.models import Mailbox

                Mailbox.objects.all().delete()
                with mock.patch(
                    "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox",
                    side_effect=Rejected(message, operation="ensure_mailbox"),
                ):
                    res = self._create_mailbox()
                assert_clean(self, res.content.decode(), context="POST /api/mailboxes/")

    def test_mailbox_response_is_clean_when_engine_raises_raw_exception(self):
        """
        The L1 scenario exactly: an unexpected non-typed exception carrying the
        engine's hostname and port must not reach the customer.
        """
        raw = RuntimeError(
            "HTTPConnectionPool(host='mailcow-nginx', port=8080): "
            "Max retries exceeded with url: /api/v1/add/mailbox"
        )
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox", side_effect=raw
        ):
            res = self._create_mailbox()
        assert_clean(self, res.content.decode(), context="POST /api/mailboxes/ (raw exc)")

    def test_stored_field_is_clean_after_raw_exception(self):
        """The leak was persisted, so check the database too, not just the response."""
        from apps.mailboxes.models import Mailbox

        raw = RuntimeError("mailcow-nginx:8080 /api/v1/add/mailbox exploded")
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox", side_effect=raw
        ):
            self._create_mailbox()
        stored = Mailbox.objects.get(local_part="alice").mail_engine_error
        assert_clean(self, stored, context="persisted mail_engine_error")
        self.assertTrue(stored, "a failure should still leave a customer-readable message")

    def test_domain_detail_is_clean_after_failed_provisioning(self):
        from apps.domains.models import Domain

        domain = Domain.objects.get(pk=self.domain.pk)
        domain.mail_engine_error = MailEngineError.customer_message
        domain.save(update_fields=["mail_engine_error"])
        res = self.client_api.get(f"/api/domains/{domain.id}/")
        assert_clean(self, res.content.decode(), context="GET /api/domains/{id}/")

    def test_reprovision_error_response_is_clean(self):
        res = self._create_mailbox()
        mailbox_id = res.data["id"]
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox",
            side_effect=EngineUnavailable(
                "HTTPConnectionPool(host='mailcow-nginx', port=8080)"
            ),
        ):
            res = self.client_api.post(
                f"/api/mailboxes/{mailbox_id}/reprovision/",
                {"password": "Another-Passphrase-9"},
                format="json",
            )
        self.assertEqual(res.status_code, 503)
        assert_clean(self, res.content.decode(), context="POST reprovision")

    def test_status_toggle_error_response_is_clean(self):
        res = self._create_mailbox()
        mailbox_id = res.data["id"]
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.set_mailbox_active",
            side_effect=EngineUnavailable("mailcow-nginx:8080 unreachable"),
        ):
            res = self.client_api.patch(
                f"/api/mailboxes/{mailbox_id}/status/", {"status": "disabled"}, format="json"
            )
        self.assertEqual(res.status_code, 503)
        assert_clean(self, res.content.decode(), context="PATCH mailbox status")

    def test_customer_serializers_expose_no_engine_vocabulary(self):
        """Field *names* are part of the contract too."""
        self._create_mailbox()
        for path in ["/api/domains/", "/api/mailboxes/", "/api/aliases/", "/api/forwarding/"]:
            with self.subTest(path=path):
                res = self.client_api.get(path)
                body = res.content.decode()
                self.assertNotIn("mail_engine", body, f"{path} exposes engine field naming")
                assert_clean(self, body, context=path)

    def test_forwarding_failure_response_is_clean(self):
        res = self._create_mailbox()
        mailbox_id = res.data["id"]
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_forwarding",
            side_effect=EngineUnavailable("mailcow-nginx:8080 /api/v1/add/alias failed"),
        ):
            res = self.client_api.post(
                "/api/forwarding/",
                {
                    "source_mailbox_id": mailbox_id,
                    "destination_email": "elsewhere@example.test",
                    "keep_copy": True,
                },
                format="json",
            )
        assert_clean(self, res.content.decode(), context="POST /api/forwarding/")
