"""
The public health endpoint must not disclose infrastructure.

Before P1, `/api/health/` was public and returned a per-component breakdown
including `mail_engine`. That tells an unauthenticated caller that a distinct
Mail Engine exists and whether it is up — infrastructure detail, and a DEC-011
breach. Component detail now lives behind the internal secret.
"""
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

INTERNAL_SECRET = "p1-health-test-internal-secret"

# Terms that must never appear in the public payload.
INFRA_TERMS = [
    "mail_engine", "mail engine", "mailcow", "postfix", "dovecot", "rspamd",
    "redis", "postgres", "database", "cache", "latency", "checks", "version",
]


class PublicHealthTest(TestCase):
    def test_public_health_is_reachable_without_auth(self):
        res = APIClient().get("/api/health/")
        self.assertEqual(res.status_code, 200)

    def test_public_health_discloses_no_components(self):
        body = APIClient().get("/api/health/").content.decode().lower()
        for term in INFRA_TERMS:
            with self.subTest(term=term):
                self.assertNotIn(term, body, f"public health leaked {term!r}: {body}")

    def test_public_health_payload_is_exactly_two_keys(self):
        data = APIClient().get("/api/health/").data
        self.assertEqual(set(data.keys()), {"status", "service"})
        self.assertEqual(data["service"], "MateMail")
        self.assertEqual(data["status"], "ok")

    def test_public_health_does_not_consult_the_mail_engine(self):
        """
        Mail-side trouble must not take the web tier out of a load balancer, and
        the probe must not become a way to poll engine state from outside.
        """
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.check_health"
        ) as check:
            APIClient().get("/api/health/")
        check.assert_not_called()

    def test_old_detailed_subroutes_are_gone(self):
        for path in ["/api/health/db/", "/api/health/redis/", "/api/health/mail-engine/"]:
            with self.subTest(path=path):
                self.assertEqual(APIClient().get(path).status_code, 404)


@override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
class InternalHealthTest(TestCase):
    def test_internal_health_is_under_the_protected_prefix(self):
        self.assertTrue(reverse("health-internal").startswith("/api/internal/"))

    def test_internal_health_requires_the_secret(self):
        res = APIClient().get(reverse("health-internal"))
        self.assertEqual(res.status_code, 403)

    def test_internal_health_rejects_a_wrong_secret(self):
        res = APIClient().get(
            reverse("health-internal"), HTTP_X_INTERNAL_SECRET="not-the-secret"
        )
        self.assertEqual(res.status_code, 403)

    def test_internal_health_returns_component_detail(self):
        res = APIClient().get(
            reverse("health-internal"), HTTP_X_INTERNAL_SECRET=INTERNAL_SECRET
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("checks", res.data)
        for component in ("database", "cache", "mail_engine"):
            self.assertIn(component, res.data["checks"])

    @override_settings(INTERNAL_API_SECRET="")
    def test_unconfigured_secret_fails_closed(self):
        res = APIClient().get(reverse("health-internal"), HTTP_X_INTERNAL_SECRET="")
        self.assertEqual(res.status_code, 403)

    def test_engine_failure_does_not_break_the_internal_endpoint(self):
        from apps.mail_engine.dto import EngineHealth

        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.check_health",
            return_value=EngineHealth(reachable=False, detail="unreachable"),
        ):
            res = APIClient().get(
                reverse("health-internal"), HTTP_X_INTERNAL_SECRET=INTERNAL_SECRET
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "degraded")
        self.assertEqual(res.data["checks"]["mail_engine"]["status"], "error")
