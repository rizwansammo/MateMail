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

    def test_the_webmail_sso_bridge_is_gone(self):
        """
        P11 removed it. It minted a mailbox login token for any mailbox in the
        caller's organization, with no check that the caller owned that
        mailbox — so an administrator could have opened an employee's mail the
        moment a webmail client existed to spend the token. PostBox
        authenticates the mailbox user directly instead (DEC-049).
        """
        resolver = get_resolver()
        for path in ("/api/webmail/sso/", "/api/internal/webmail/validate-token/",
                     "/api/webmail/validate-token/"):
            with self.subTest(path=path):
                with self.assertRaises(Exception):
                    resolver.resolve(path)

    def test_no_route_can_mint_a_token_for_another_mailbox(self):
        """
        The rule, not just the two paths above: nothing in the URL conf may
        accept a mailbox identifier and hand back a credential for it.
        """
        for name in ("webmail-sso", "webmail-validate-token"):
            with self.subTest(name=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name)

    # ── Secret enforcement ───────────────────────────────────────────────────

    def test_internal_endpoints_reject_a_missing_secret(self):
        cases = [
            ("smtp-inbound-policy", {"recipient": "a@b.example"}),
            ("smtp-outbound-policy", {"sender": "a@b.example", "sasl_username": "a@b.example"}),
        ]
        for name, body in cases:
            with self.subTest(view=name):
                res = APIClient().post(reverse(name), body, format="json")
                self.assertEqual(res.status_code, 403)

    def test_internal_endpoints_reject_a_wrong_secret(self):
        # Uses the inbound SMTP policy because the webmail validator these
        # cases were written against was removed with the SSO bridge in P11.
        res = APIClient().post(
            reverse("smtp-inbound-policy"),
            {"recipient": "a@b.example"},
            format="json",
            HTTP_X_INTERNAL_SECRET="not-the-secret",
        )
        self.assertEqual(res.status_code, 403)

    def test_internal_endpoint_accepts_the_correct_secret(self):
        """Correct secret gets past auth; the answer itself may still be a refusal."""
        res = APIClient().post(
            reverse("smtp-inbound-policy"),
            {"recipient": "nobody@no-such-domain.example"},
            format="json",
            HTTP_X_INTERNAL_SECRET=INTERNAL_SECRET,
        )
        self.assertNotEqual(res.status_code, 403)

    @override_settings(INTERNAL_API_SECRET="")
    def test_unconfigured_secret_denies_rather_than_allows(self):
        """An empty secret must fail closed, never match an empty header."""
        res = APIClient().post(
            reverse("smtp-inbound-policy"),
            {"recipient": "a@b.example"},
            format="json",
            HTTP_X_INTERNAL_SECRET="",
        )
        self.assertEqual(res.status_code, 403)


class InternalSecretComparisonTest(TestCase):
    """The comparison must be constant-time, not ==."""

    def test_authorized_helpers_use_compare_digest(self):
        import inspect

        from apps.smtp_policy import views as smtp_views

        # apps.webmail no longer has an `_authorized` — its views were removed
        # with the SSO bridge in P11.
        for module in (smtp_views,):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module._authorized)
                self.assertIn("compare_digest", source)
                self.assertNotIn("== expected", source)
