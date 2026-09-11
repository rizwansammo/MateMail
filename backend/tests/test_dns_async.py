"""
Asynchronous DNS checking: enqueue behaviour, deduplication, fan-out.

Before P3 the customer-triggered check resolved inline — four lookups at a 5s
timeout each, inside a request worker — and the periodic sweep was one serial
loop, so a single slow domain delayed every domain behind it.
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.dnshealth.tasks import (
    BACKGROUND_MIN_INTERVAL_SECONDS,
    background_check_allowed,
    check_all_active_domains_dns,
    check_domain_dns,
    claim_check_slot,
    release_check_slot,
)
from apps.domains.models import DomainStatus
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
        "LOCATION": "dns-async-tests",
    }
}


@override_settings(CACHES=LOCMEM_CACHE)
class SlotTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_slot_is_exclusive(self):
        self.assertTrue(claim_check_slot("d1"))
        self.assertFalse(claim_check_slot("d1"), "a second claim succeeded")

    def test_slot_can_be_released_and_reclaimed(self):
        claim_check_slot("d2")
        release_check_slot("d2")
        self.assertTrue(claim_check_slot("d2"))

    def test_slots_are_per_domain(self):
        self.assertTrue(claim_check_slot("d3"))
        self.assertTrue(claim_check_slot("d4"))

    def test_background_budget_is_one_per_window(self):
        self.assertTrue(background_check_allowed("d5"))
        self.assertFalse(background_check_allowed("d5"))
        self.assertEqual(BACKGROUND_MIN_INTERVAL_SECONDS, 300)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class CheckEndpointTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("dnsapi@example.test")
        self.tenant = make_tenant(self.owner, name="DnsApi", slug="dns-api")
        self.domain = make_domain(self.tenant, "async.example")
        self.client_api = auth_client(self.owner, self.tenant)

    def test_endpoint_enqueues_and_returns_202(self):
        with mock.patch("apps.dnshealth.views.check_domain_dns.delay") as delay:
            res = self.client_api.post(f"/api/domains/{self.domain.id}/check/")
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.data["status"], "queued")
        delay.assert_called_once_with(str(self.domain.id))

    def test_endpoint_never_resolves_dns_inline(self):
        """The whole point: no resolver work inside the request."""
        with mock.patch("apps.dnshealth.views.check_domain_dns.delay"), \
             mock.patch("dns.resolver.resolve") as resolve:
            self.client_api.post(f"/api/domains/{self.domain.id}/check/")
        resolve.assert_not_called()

    def test_repeated_clicks_are_deduplicated(self):
        with mock.patch("apps.dnshealth.views.check_domain_dns.delay") as delay:
            first = self.client_api.post(f"/api/domains/{self.domain.id}/check/")
            second = self.client_api.post(f"/api/domains/{self.domain.id}/check/")
            third = self.client_api.post(f"/api/domains/{self.domain.id}/check/")
        self.assertEqual(delay.call_count, 1, "duplicate work was queued")
        self.assertEqual(first.data["status"], "queued")
        self.assertEqual(second.data["status"], "already_running")
        self.assertEqual(third.data["status"], "already_running")
        # Still 202 — the caller's intent is satisfied either way.
        self.assertEqual(second.status_code, 202)

    def test_response_never_claims_a_fresh_result(self):
        with mock.patch("apps.dnshealth.views.check_domain_dns.delay"):
            res = self.client_api.post(f"/api/domains/{self.domain.id}/check/")
        self.assertIn("started", res.data["detail"].lower())
        self.assertIn("domain", res.data)  # last known state, labelled as such

    def test_broker_failure_releases_the_slot_and_reports_503(self):
        with mock.patch(
            "apps.dnshealth.views.check_domain_dns.delay", side_effect=RuntimeError("broker down")
        ):
            res = self.client_api.post(f"/api/domains/{self.domain.id}/check/")
        self.assertEqual(res.status_code, 503)
        # Slot released, so a retry is possible rather than wedged.
        self.assertTrue(claim_check_slot(self.domain.id))

    def test_other_tenant_cannot_trigger_a_check(self):
        other = make_user("dnsother@example.test")
        other_tenant = make_tenant(other, name="DnsOther", slug="dns-other")
        res = auth_client(other, other_tenant).post(
            f"/api/domains/{self.domain.id}/check/"
        )
        self.assertEqual(res.status_code, 404)


@override_settings(CACHES=LOCMEM_CACHE)
class TaskTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("dnstask@example.test")
        self.tenant = make_tenant(self.owner, name="DnsTask", slug="dns-task")
        self.domain = make_domain(self.tenant, "task.example")

    def test_task_releases_its_slot_on_success(self):
        claim_check_slot(self.domain.id)
        with mock.patch("apps.dnshealth.services.check_dns_for_domain", return_value=self.domain):
            check_domain_dns(str(self.domain.id))
        self.assertTrue(claim_check_slot(self.domain.id), "slot was not released")

    def test_task_releases_its_slot_on_failure(self):
        """A crash must not wedge a domain into a permanently un-checkable state."""
        claim_check_slot(self.domain.id)
        with mock.patch(
            "apps.dnshealth.services.check_dns_for_domain", side_effect=RuntimeError("resolver blew up")
        ), mock.patch.object(check_domain_dns, "retry", side_effect=RuntimeError("retried")):
            with self.assertRaises(RuntimeError):
                check_domain_dns(str(self.domain.id))
        self.assertTrue(claim_check_slot(self.domain.id), "slot leaked after failure")

    def test_task_handles_a_deleted_domain(self):
        import uuid

        check_domain_dns(str(uuid.uuid4()))  # must not raise


@override_settings(CACHES=LOCMEM_CACHE)
class SweepTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("sweep@example.test")
        self.tenant = make_tenant(self.owner, name="Sweep", slug="sweep")
        self.domains = [
            make_domain(self.tenant, f"sweep{i}.example") for i in range(5)
        ]

    def test_sweep_fans_out_one_task_per_domain(self):
        with mock.patch("apps.dnshealth.tasks.check_domain_dns.delay") as delay:
            result = check_all_active_domains_dns()
        self.assertEqual(delay.call_count, 5)
        self.assertEqual(result["enqueued"], 5)
        self.assertEqual(result["total"], 5)

    def test_sweep_never_resolves_dns_itself(self):
        with mock.patch("apps.dnshealth.tasks.check_domain_dns.delay"), \
             mock.patch("dns.resolver.resolve") as resolve:
            check_all_active_domains_dns()
        resolve.assert_not_called()

    def test_sweep_skips_paused_domains(self):
        self.domains[0].status = DomainStatus.PAUSED
        self.domains[0].save(update_fields=["status"])
        with mock.patch("apps.dnshealth.tasks.check_domain_dns.delay") as delay:
            result = check_all_active_domains_dns()
        self.assertEqual(delay.call_count, 4)
        self.assertEqual(result["total"], 4)

    def test_one_bad_domain_does_not_abort_the_sweep(self):
        """The failure mode the old serial loop had."""
        calls = {"n": 0}

        def flaky(domain_id):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("enqueue failed for this one")

        with mock.patch("apps.dnshealth.tasks.check_domain_dns.delay", side_effect=flaky):
            result = check_all_active_domains_dns()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["enqueued"], 4, "the sweep stopped at the failure")

    def test_sweep_respects_the_background_budget(self):
        with mock.patch("apps.dnshealth.tasks.check_domain_dns.delay") as delay:
            first = check_all_active_domains_dns()
            second = check_all_active_domains_dns()
        self.assertEqual(first["enqueued"], 5)
        self.assertEqual(second["enqueued"], 0, "the 5-minute budget was ignored")
        self.assertEqual(second["skipped"], 5)
        self.assertEqual(delay.call_count, 5)

    def test_sweep_does_not_pile_onto_a_manual_check(self):
        claim_check_slot(self.domains[0].id)
        with mock.patch("apps.dnshealth.tasks.check_domain_dns.delay") as delay:
            result = check_all_active_domains_dns()
        self.assertEqual(delay.call_count, 4)
        self.assertEqual(result["skipped"], 1)
