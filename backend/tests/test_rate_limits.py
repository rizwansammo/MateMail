"""
Web and API abuse limits.

These assert behaviour, not configuration: every test drives the real endpoint
until it refuses, because a limit that exists in settings and never fires is
the failure mode worth catching.

Each class pins its own cache so counters cannot leak between tests, and the
DRF throttles are switched off where they would mask the limit under test —
the coarse 5/min per-IP `auth` scope would otherwise trip before a 3/hour
signup limit could be demonstrated.
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.security import ratelimit
from apps.security.client_ip import get_client_ip
from apps.security.limits import (
    DOMAIN_CHECK_PER_DOMAIN,
    FORGOT_PASSWORD_PER_EMAIL,
    LOGIN_PER_ACCOUNT,
    LOGIN_PER_IP,
    MAILBOX_CREATE_PER_TENANT,
    SIGNUP_PER_IP,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "rate-limit-tests",
    }
}


class ClientIPTest(TestCase):
    """
    The identity every per-IP limit is keyed on.

    DRF's own get_ident joins the whole X-Forwarded-For header when NUM_PROXIES
    is unset, so a caller varying the header gets a fresh bucket per request.
    """

    def _request(self, remote_addr="10.0.0.1", forwarded=None):
        request = mock.Mock()
        request.META = {"REMOTE_ADDR": remote_addr}
        if forwarded is not None:
            request.META["HTTP_X_FORWARDED_FOR"] = forwarded
        return request

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_forwarded_header_is_ignored_without_a_trusted_proxy(self):
        req = self._request(forwarded="1.2.3.4")
        self.assertEqual(get_client_ip(req), "10.0.0.1")

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_one_hop_takes_the_entry_nginx_appended(self):
        # nginx uses $proxy_add_x_forwarded_for: the client's claim, then the
        # address nginx actually saw. Only the rightmost entry is ours.
        req = self._request(forwarded="203.0.113.9, 198.51.100.7")
        self.assertEqual(get_client_ip(req), "198.51.100.7")

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_a_spoofed_chain_cannot_choose_the_bucket(self):
        """The whole point: attacker-supplied entries never become the identity."""
        first = self._request(forwarded="9.9.9.9, 198.51.100.7")
        second = self._request(forwarded="8.8.8.8, 198.51.100.7")
        self.assertEqual(get_client_ip(first), get_client_ip(second))

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_short_chain_falls_back_to_the_socket_peer(self):
        req = self._request(remote_addr="10.0.0.5", forwarded="")
        self.assertEqual(get_client_ip(req), "10.0.0.5")

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_malformed_entry_does_not_become_a_key(self):
        req = self._request(remote_addr="10.0.0.5", forwarded="not-an-ip")
        self.assertEqual(get_client_ip(req), "10.0.0.5")

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_missing_address_is_none_not_a_caller_controlled_value(self):
        req = mock.Mock()
        req.META = {"HTTP_X_FORWARDED_FOR": "1.2.3.4"}
        self.assertIsNone(get_client_ip(req))

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_port_suffix_is_stripped(self):
        req = self._request(forwarded="203.0.113.9, 198.51.100.7:41234")
        self.assertEqual(get_client_ip(req), "198.51.100.7")


@override_settings(CACHES=LOCMEM_CACHE)
class PrimitiveTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_allows_up_to_the_limit_then_refuses(self):
        for _ in range(3):
            self.assertTrue(
                ratelimit.hit("t", "a", limit=3, window=60).allowed
            )
        self.assertFalse(ratelimit.hit("t", "a", limit=3, window=60).allowed)

    def test_buckets_are_per_identity(self):
        for _ in range(3):
            ratelimit.hit("t", "a", limit=3, window=60)
        self.assertTrue(ratelimit.hit("t", "b", limit=3, window=60).allowed)

    def test_reset_clears_the_window(self):
        for _ in range(3):
            ratelimit.hit("t", "a", limit=3, window=60)
        ratelimit.reset("t", "a", window=60)
        self.assertTrue(ratelimit.hit("t", "a", limit=3, window=60).allowed)

    def test_check_does_not_consume(self):
        for _ in range(3):
            ratelimit.check("t", "a", limit=3, window=60)
        self.assertTrue(ratelimit.hit("t", "a", limit=3, window=60).allowed)

    def test_missing_identity_shares_one_bucket(self):
        """An unidentifiable caller must not get an unlimited private bucket."""
        for _ in range(3):
            ratelimit.hit("t", None, limit=3, window=60)
        self.assertFalse(ratelimit.hit("t", "", limit=3, window=60).allowed)

    def test_identity_is_case_insensitive(self):
        for _ in range(3):
            ratelimit.hit("t", "User@Example.com", limit=3, window=60)
        self.assertFalse(
            ratelimit.hit("t", "user@example.com", limit=3, window=60).allowed
        )

    def test_retry_after_is_a_usable_number(self):
        decision = ratelimit.hit("t", "a", limit=1, window=900)
        self.assertGreater(decision.retry_after, 0)
        self.assertLessEqual(decision.retry_after, 900)

    def test_claim_once_is_single_use(self):
        self.assertTrue(ratelimit.claim_once("b", "v", ttl=60))
        self.assertFalse(ratelimit.claim_once("b", "v", ttl=60))


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class LoginLimitTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("victim@example.test")

    def _login(self, password="wrong", email=None, ip="198.51.100.10"):
        return self.client.post(
            "/api/auth/login/",
            {"email": email or self.user.email, "password": password},
            REMOTE_ADDR=ip,
        )

    def test_repeated_failures_are_refused_with_429(self):
        for _ in range(LOGIN_PER_IP.limit):
            self.assertEqual(self._login().status_code, 401)
        blocked = self._login()
        self.assertEqual(blocked.status_code, 429)

    def test_refusal_carries_retry_after(self):
        for _ in range(LOGIN_PER_IP.limit):
            self._login()
        blocked = self._login()
        self.assertIn("Retry-After", blocked.headers)
        self.assertGreater(int(blocked.headers["Retry-After"]), 0)

    def test_lockout_message_does_not_name_the_account(self):
        for _ in range(LOGIN_PER_IP.limit):
            self._login()
        body = str(self._login().data)
        self.assertNotIn(self.user.email, body)

    def test_the_account_is_locked_from_a_different_address(self):
        """Per-account, so rotating source addresses does not buy more guesses."""
        for i in range(LOGIN_PER_ACCOUNT.limit):
            self._login(ip=f"198.51.100.{20 + i}")
        blocked = self._login(ip="203.0.113.77")
        self.assertEqual(blocked.status_code, 429)

    def test_case_variations_share_one_account_bucket(self):
        for _ in range(LOGIN_PER_ACCOUNT.limit):
            self._login(email=self.user.email.upper(), ip="198.51.100.90")
        blocked = self._login(ip="203.0.113.90")
        self.assertEqual(blocked.status_code, 429)

    def test_a_successful_login_clears_the_counters(self):
        for _ in range(LOGIN_PER_IP.limit - 1):
            self._login()
        ok = self._login(password=TEST_PASSWORD)
        self.assertEqual(ok.status_code, 200)
        # Would be the sixth attempt without the reset.
        self.assertEqual(self._login().status_code, 401)

    def test_mixed_case_address_still_authenticates(self):
        """
        The limit key is lowercased; the credential must not be.

        UserManager.normalize_email lowercases only the domain, so folding the
        whole address before authenticate() would lock out every account with a
        capital in the local part.
        """
        mixed = make_user("Mixed.Case@Example.test")
        res = self.client.post(
            "/api/auth/login/",
            {"email": mixed.email, "password": TEST_PASSWORD},
            REMOTE_ADDR="198.51.100.111",
        )
        self.assertEqual(res.status_code, 200)

    def test_unknown_and_known_accounts_answer_identically(self):
        known = self._login()
        unknown = self._login(email="nobody@example.test", ip="198.51.100.55")
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.data["detail"], unknown.data["detail"])

    def test_a_disabled_account_is_not_distinguishable(self):
        disabled = make_user("disabled@example.test", active=False)
        res = self.client.post(
            "/api/auth/login/",
            {"email": disabled.email, "password": TEST_PASSWORD},
            REMOTE_ADDR="198.51.100.66",
        )
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.data["detail"], "Invalid credentials.")


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class SignupLimitTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)

    def _signup(self, n, ip="198.51.100.30"):
        return self.client.post(
            "/api/auth/signup/",
            {
                "email": f"new{n}@example.test",
                "password": TEST_PASSWORD,
                "full_name": f"New {n}",
                "workspace_name": f"Workspace {n}",
            },
            REMOTE_ADDR=ip,
        )

    def test_signups_from_one_address_are_capped(self):
        for n in range(SIGNUP_PER_IP.limit):
            self.assertEqual(self._signup(n).status_code, 201)
        self.assertEqual(self._signup(99).status_code, 429)

    def test_another_address_is_unaffected(self):
        for n in range(SIGNUP_PER_IP.limit):
            self._signup(n)
        self.assertEqual(self._signup(50, ip="203.0.113.30").status_code, 201)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class ForgotPasswordLimitTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.user = make_user("reset@example.test")

    def _forgot(self, email=None, ip="198.51.100.40"):
        return self.client.post(
            "/api/auth/forgot-password/",
            {"email": email or self.user.email},
            REMOTE_ADDR=ip,
        )

    def test_requests_for_one_address_are_capped(self):
        for _ in range(FORGOT_PASSWORD_PER_EMAIL.limit):
            self.assertEqual(self._forgot().status_code, 200)
        self.assertEqual(self._forgot().status_code, 429)

    def test_the_cap_does_not_reveal_whether_an_account_exists(self):
        """
        An unregistered address must hit the limit exactly as a registered one
        does. Counting only real accounts would answer 429 for those and 200
        forever for the rest — the oracle the uniform 200 exists to prevent.
        """
        ghost = "nosuchuser@example.test"
        for _ in range(FORGOT_PASSWORD_PER_EMAIL.limit):
            self.assertEqual(self._forgot(email=ghost).status_code, 200)
        self.assertEqual(self._forgot(email=ghost).status_code, 429)

    def test_the_limit_survives_a_change_of_address(self):
        for _ in range(FORGOT_PASSWORD_PER_EMAIL.limit):
            self._forgot(ip="198.51.100.41")
        self.assertEqual(self._forgot(ip="203.0.113.41").status_code, 429)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class DomainCheckLimitTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("dnslimit@example.test")
        self.tenant = make_tenant(self.owner, name="DnsLimit", slug="dns-limit")
        self.domain = make_domain(self.tenant, "limited.example")
        self.api = auth_client(self.owner, self.tenant)

    def test_dns_checks_per_domain_are_capped(self):
        with mock.patch("apps.dnshealth.views.check_domain_dns.delay"):
            for _ in range(DOMAIN_CHECK_PER_DOMAIN.limit):
                res = self.api.post(f"/api/domains/{self.domain.id}/check/")
                self.assertEqual(res.status_code, 202)
            blocked = self.api.post(f"/api/domains/{self.domain.id}/check/")
        self.assertEqual(blocked.status_code, 429)

    def test_ownership_checks_share_the_domain_budget(self):
        """Both endpoints resolve DNS for the same name; one budget covers both."""
        with mock.patch(
            "apps.domains.verification.check_ownership_dns",
            return_value=(False, "not found"),
        ):
            for _ in range(DOMAIN_CHECK_PER_DOMAIN.limit):
                self.api.post(f"/api/domains/{self.domain.id}/verify-ownership/")
            blocked = self.api.post(f"/api/domains/{self.domain.id}/verify-ownership/")
        self.assertEqual(blocked.status_code, 429)

    def test_another_domain_has_its_own_budget(self):
        other = make_domain(self.tenant, "other-limited.example")
        with mock.patch("apps.dnshealth.views.check_domain_dns.delay"):
            for _ in range(DOMAIN_CHECK_PER_DOMAIN.limit):
                self.api.post(f"/api/domains/{self.domain.id}/check/")
            res = self.api.post(f"/api/domains/{other.id}/check/")
        self.assertEqual(res.status_code, 202)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class MailboxCreateLimitTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("mblimit@example.test")
        self.tenant = make_tenant(self.owner, name="MbLimit", slug="mb-limit")
        self.domain = make_domain(self.tenant, "mblimit.example")
        self.api = auth_client(self.owner, self.tenant)

    def _create(self, n):
        return self.api.post(
            "/api/mailboxes/",
            {
                "local_part": f"user{n}",
                "domain_id": str(self.domain.id),
                "full_name": f"User {n}",
                "password": TEST_PASSWORD,
                "quota_mb": 1024,
            },
            format="json",
        )

    def test_creation_rate_is_capped_per_tenant(self):
        created = 0
        for n in range(MAILBOX_CREATE_PER_TENANT.limit):
            res = self._create(n)
            # Plan limits may refuse first; this test is about the rate limit,
            # so only assert that nothing is a 429 inside the allowance.
            self.assertNotEqual(res.status_code, 429)
            created += 1
        self.assertEqual(created, MAILBOX_CREATE_PER_TENANT.limit)
        self.assertEqual(self._create(999).status_code, 429)


@override_settings(CACHES=LOCMEM_CACHE)
class ExemptPathTest(TestCase):
    """
    The internal bridge and health checks must never be rate limited.

    /api/internal/ is what Postfix consults per message once the Mail Engine is
    live. A 60/min cap there is a mail outage, not an abuse control.
    """

    def setUp(self):
        cache.clear()

    def test_health_is_not_throttled(self):
        for _ in range(80):
            res = self.client.get("/api/health/")
            self.assertNotEqual(res.status_code, 429)

    def test_internal_prefix_is_not_throttled(self):
        for _ in range(80):
            res = self.client.post(
                "/api/internal/smtp/inbound/", {}, content_type="application/json"
            )
            self.assertNotEqual(res.status_code, 429)
