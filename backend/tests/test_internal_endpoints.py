"""
Internal endpoints must live under /api/internal/ and require the shared secret.

Before Phase 0 the webmail token validator was documented as
/api/internal/webmail/validate-token/ but was actually mounted at
/api/webmail/validate-token/. The edge nginx rule denies /api/internal/, so the
documented protection did not apply to the real path and the endpoint was
publicly reachable.

These tests pin the URL layout, so an endpoint cannot drift back outside the
prefix that nginx protects.
"""
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, get_resolver, reverse
from rest_framework.test import APIClient

INTERNAL_SECRET = "phase0-test-internal-secret-value"

# Every view that authenticates with INTERNAL_API_SECRET rather than a user
# credential. All of them must sit under /api/internal/.
INTERNAL_VIEW_NAMES = [
    "smtp-inbound-policy",
    "smtp-outbound-policy",
    "smtp-rate-limits",
    "webmail-validate-token",
]


@override_settings(INTERNAL_API_SECRET=INTERNAL_SECRET)
class InternalEndpointRoutingTest(TestCase):
    def test_every_internal_view_is_under_api_internal(self):
        for name in INTERNAL_VIEW_NAMES:
            with self.subTest(view=name):
                path = reverse(name)
                self.assertTrue(
                    path.startswith("/api/internal/"),
                    f"{name} resolves to {path}, outside the nginx-protected prefix",
                )

    def test_validate_token_is_no_longer_publicly_routed(self):
        """The old public path must not resolve to anything at all."""
        resolver = get_resolver()
        with self.assertRaises(Exception):
            resolver.resolve("/api/webmail/validate-token/")

    def test_tenant_facing_sso_route_stays_outside_internal(self):
        path = reverse("webmail-sso")
        self.assertEqual(path, "/api/webmail/sso/")
        self.assertFalse(path.startswith("/api/internal/"))

    # ── Secret enforcement ───────────────────────────────────────────────────

    def test_internal_endpoints_reject_a_missing_secret(self):
        cases = [
            ("webmail-validate-token", {"token": "x", "email": "a@b.example"}),
            ("smtp-inbound-policy", {"recipient": "a@b.example"}),
            ("smtp-outbound-policy", {"sender": "a@b.example", "sasl_username": "a@b.example"}),
        ]
        for name, body in cases:
            with self.subTest(view=name):
                res = APIClient().post(reverse(name), body, format="json")
                self.assertEqual(res.status_code, 403)

    def test_internal_endpoints_reject_a_wrong_secret(self):
        res = APIClient().post(
            reverse("webmail-validate-token"),
            {"token": "x", "email": "a@b.example"},
            format="json",
            HTTP_X_INTERNAL_SECRET="not-the-secret",
        )
        self.assertEqual(res.status_code, 403)

    def test_internal_endpoint_accepts_the_correct_secret(self):
        """Correct secret gets past auth (the token itself is still invalid)."""
        res = APIClient().post(
            reverse("webmail-validate-token"),
            {"token": "no-such-sso-token", "email": "a@b.example"},
            format="json",
            HTTP_X_INTERNAL_SECRET=INTERNAL_SECRET,
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data["valid"])

    @override_settings(INTERNAL_API_SECRET="")
    def test_unconfigured_secret_denies_rather_than_allows(self):
        """An empty secret must fail closed, never match an empty header."""
        res = APIClient().post(
            reverse("webmail-validate-token"),
            {"token": "x", "email": "a@b.example"},
            format="json",
            HTTP_X_INTERNAL_SECRET="",
        )
        self.assertEqual(res.status_code, 403)


class InternalSecretComparisonTest(TestCase):
    """The comparison must be constant-time, not ==."""

    def test_authorized_helpers_use_compare_digest(self):
        import inspect

        from apps.smtp_policy import views as smtp_views
        from apps.webmail import views as webmail_views

        for module in (smtp_views, webmail_views):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module._authorized)
                self.assertIn("compare_digest", source)
                self.assertNotIn("== expected", source)
