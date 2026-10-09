"""P4-C.C backend authorization and DNS verification regression tests."""
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone

from apps.transport_security.dns import check_domain_policy_dns, policy_hostname
from apps.transport_security.models import DomainTransportSecurity, TransportSecurityLifecycle
from tests.factories import (
    FAST_PASSWORD_HASHERS, auth_client, disable_throttling,
    make_domain, make_tenant, make_unverified_domain, make_user,
)

FLAGS = dict(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=True,
    TRANSPORT_SECURITY_PROVISIONING_ENABLED=True,
    TRANSPORT_SECURITY_PROVISIONER_SECRET="tests-only-long-secret",
    MTA_STS_POLICY_EDGE_TARGET="mta-sts-gateway.matemail.pro",
    MTA_STS_POLICY_EDGE_READY=True,
    MAIL_HOSTNAME="mx.matemail.pro",
)


def records(name, value):
    if name == "mta-sts.company.example":
        return [SimpleNamespace(target=SimpleNamespace(__str__=lambda self: value))]
    if name == "company.example":
        return [SimpleNamespace(exchange=SimpleNamespace(__str__=lambda self: value))]
    raise ValueError("Unexpected DNS hostname")


@override_settings(**FLAGS)
class TransportHTTPSAPITest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("policy-owner@example.test")
        self.tenant = make_tenant(self.owner, "Policy", "policy-test")
        self.domain = make_domain(self.tenant, "company.example")
        self.client = auth_client(self.owner, self.tenant)
        self.base = f"/api/domains/{self.domain.pk}/transport-security/"
        self.client.post(self.base, {"enabled": True}, format="json")
        self.internal = "/api/internal/transport-security/"
        self.header = {"HTTP_X_MATEMAIL_TRANSPORT_SECRET": "tests-only-long-secret"}

    def dns_success(self):
        return mock.patch(
            "apps.transport_security.views.check_domain_policy_dns",
            return_value=(True, "Verified."),
        )

    def test_verify_dns_sets_queue_state_without_certs(self):
        with self.dns_success():
            result = self.client.post(self.base + "verify-dns/", {}, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data["verified"])
        row = DomainTransportSecurity.objects.get(domain=self.domain)
        self.assertIsNotNone(row.dns_verified_at)
        self.assertEqual(row.lifecycle, "pending_dns")
        self.assertIsNone(row.cert_verified_at)

    def test_unverified_dns_never_queues_worker(self):
        with mock.patch(
            "apps.transport_security.views.check_domain_policy_dns",
            return_value=(False, "CNAME missing"),
        ):
            result = self.client.post(self.base + "verify-dns/", {}, format="json")
        self.assertEqual(result.status_code, 409)
        row = DomainTransportSecurity.objects.get(domain=self.domain)
        self.assertIsNone(row.dns_verified_at)

    def test_verify_other_tenant_domain_is_404(self):
        alien = make_user("alien@tenant.example")
        t2 = make_tenant(alien, "Elsewhere", "elsewhere-ts")
        client = auth_client(alien, t2)
        self.assertEqual(client.post(self.base + "verify-dns/", {}).status_code, 404)

    def test_internal_missing_or_wrong_secret_denied(self):
        self.assertEqual(self.client.get(self.internal + "pending/").status_code, 403)
        self.assertEqual(
            self.client.post(self.internal + "authorize/", {"id": str(self.domain.id)}).status_code, 403
        )

    def test_internal_queue_requires_reverified_dns_and_identity(self):
        with self.dns_success():
            self.client.post(self.base + "verify-dns/", {}, format="json")
        with mock.patch("apps.transport_security.internal_views.check_domain_policy_dns",
                        return_value=(True, "OK")):
            queued = self.client.get(self.internal + "pending/", **self.header)
            auth = self.client.post(
                self.internal + "authorize/", {"id": str(self.domain.id)},
                format="json", **self.header,
            )
        self.assertEqual(queued.status_code, 200)
        self.assertEqual(len(queued.data["results"]), 1)
        self.assertEqual(auth.status_code, 200)
        self.assertTrue(auth.data["approved"])
        self.assertEqual(auth.data["hostname"], "mta-sts.company.example")
        self.assertEqual(auth.data["tenant_id"], str(self.tenant.id))

    def test_recheck_failure_blocks_worker_even_with_old_timestamp(self):
        with self.dns_success():
            self.client.post(self.base + "verify-dns/", {}, format="json")
        with mock.patch("apps.transport_security.internal_views.check_domain_policy_dns",
                        return_value=(False, "Owner moved")):
            queued = self.client.get(self.internal + "pending/", **self.header)
            auth = self.client.post(
                self.internal + "authorize/", {"id": str(self.domain.id)},
                format="json", **self.header,
            )
        self.assertEqual(queued.data["results"], [])
        self.assertEqual(auth.status_code, 404)

    def test_worker_cannot_claim_active_without_ready_edge(self):
        with self.dns_success():
            self.client.post(self.base + "verify-dns/", {}, format="json")
        row = DomainTransportSecurity.objects.get(domain=self.domain)
        url = self.internal + f"{self.domain.pk}/state/"
        bad = self.client.post(
            url, dict(lifecycle="active", certificate_status="active", policy_id=row.policy_id),
            format="json", **self.header,
        )
        self.assertEqual(bad.status_code, 409)

        provisioning = self.client.post(
            url, dict(lifecycle="provisioning", certificate_status="not_requested",
                      policy_id=row.policy_id), format="json", **self.header,
        )
        self.assertEqual(provisioning.status_code, 200)
        ready = self.client.post(
            url, dict(lifecycle="ready", certificate_status="active",
                      policy_id=row.policy_id), format="json", **self.header,
        )
        self.assertEqual(ready.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.lifecycle, TransportSecurityLifecycle.READY)
        self.assertIsNotNone(row.cert_verified_at)
        doc = self.client.get(self.base)
        self.assertTrue(doc.data["dns_records"][0]["publish_ready"])
        self.assertTrue(doc.data["dns_records"][1]["publish_ready"])
        self.assertFalse(doc.data["dns_records"][2]["publish_ready"])
        self.assertFalse(doc.data["can_publish_dns"])

    def test_policy_revision_mismatch_denied(self):
        with self.dns_success():
            self.client.post(self.base + "verify-dns/", {}, format="json")
        result = self.client.post(
            self.internal + f"{self.domain.pk}/state/",
            dict(lifecycle="provisioning", certificate_status="issuing", policy_id="f" * 24),
            format="json", **self.header,
        )
        self.assertEqual(result.status_code, 409)

    @override_settings(TRANSPORT_SECURITY_PROVISIONING_ENABLED=False)
    def test_backend_internal_off_by_default(self):
        self.assertEqual(self.client.get(self.internal + "pending/", **self.header).status_code, 503)

    def test_abort_if_dns_changed_after_owner_verification(self):
        with mock.patch("apps.transport_security.dns.check_ownership_dns",
                        return_value=(False, "", "")), mock.patch(
                        "apps.transport_security.dns.dns.resolver.resolve") as lookup:
            self.assertFalse(check_domain_policy_dns(self.domain)[0])
        lookup.assert_not_called()

    def test_missing_cname_fails_closed(self):
        with mock.patch("apps.transport_security.dns.check_ownership_dns",
                        return_value=(True, "", "")), mock.patch(
                        "apps.transport_security.dns.dns.resolver.resolve",
                        side_effect=__import__("dns").resolver.NXDOMAIN):
            ok, _ = check_domain_policy_dns(self.domain)
        self.assertFalse(ok)

    def test_policy_hostname_must_fit_dns_limit(self):
        with self.assertRaises(ValueError):
            policy_hostname("a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 60 + ".com")
