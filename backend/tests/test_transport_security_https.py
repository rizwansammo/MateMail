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


    def _make_ready(self):
        from django.utils import timezone
        from apps.transport_security.models import TransportSecurityCertificateStatus
        row = DomainTransportSecurity.objects.get(domain=self.domain)
        row.lifecycle = TransportSecurityLifecycle.READY
        row.certificate_status = TransportSecurityCertificateStatus.ACTIVE
        row.cert_verified_at = timezone.now()
        row.save(update_fields=["lifecycle", "certificate_status", "cert_verified_at"])
        return row

    def test_ready_policy_becomes_active_only_after_public_sts_txt(self):
        row = self._make_ready()
        with mock.patch("apps.transport_security.views.check_domain_policy_dns",
                        return_value=(True, "Verified.")), mock.patch(
            "apps.transport_security.views.check_publication_txt",
            return_value=(True, True),
        ) as verifier:
            response = self.client.post(self.base + "verify-dns/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["sts_txt_verified"])
        self.assertTrue(response.data["tls_rpt_txt_verified"])
        verifier.assert_called_once()
        row.refresh_from_db()
        self.assertEqual(row.lifecycle, TransportSecurityLifecycle.ACTIVE)
        self.assertIsNotNone(row.activated_at)
        info = self.client.get(self.base).data
        self.assertEqual([r["verification_status"] for r in info["dns_records"]],
                         ["verified", "verified", "verified"])
        self.assertIsNotNone(info["dns_records_checked_at"])

    def test_missing_sts_txt_keeps_policy_ready_not_active(self):
        row = self._make_ready()
        with mock.patch("apps.transport_security.views.check_domain_policy_dns",
                        return_value=(True, "Verified.")), mock.patch(
            "apps.transport_security.views.check_publication_txt",
            return_value=(False, True),
        ):
            response = self.client.post(self.base + "verify-dns/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["sts_txt_verified"])
        row.refresh_from_db()
        self.assertEqual(row.lifecycle, TransportSecurityLifecycle.READY)
        self.assertIsNone(row.activated_at)
        self.assertEqual(self.client.get(self.base).data["dns_records"][1]["verification_status"], "missing")

    def test_missing_tls_rpt_does_not_falsely_show_reporting_verified(self):
        row = self._make_ready()
        with mock.patch("apps.transport_security.views.check_domain_policy_dns",
                        return_value=(True, "Verified.")), mock.patch(
            "apps.transport_security.views.check_publication_txt",
            return_value=(True, False),
        ):
            response = self.client.post(self.base + "verify-dns/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["tls_rpt_txt_verified"])
        row.refresh_from_db()
        self.assertEqual(row.lifecycle, TransportSecurityLifecycle.ACTIVE)
        self.assertIsNone(row.tls_rpt_txt_verified_at)
        self.assertEqual(self.client.get(self.base).data["dns_records"][2]["verification_status"], "missing")

    def test_dns_timeout_does_not_erase_last_valid_evidence(self):
        from django.utils import timezone
        row = self._make_ready()
        checked = timezone.now()
        row.lifecycle = TransportSecurityLifecycle.ACTIVE
        row.sts_txt_verified_at = checked
        row.dns_records_checked_at = checked
        row.save(update_fields=["lifecycle", "sts_txt_verified_at", "dns_records_checked_at"])
        with mock.patch("apps.transport_security.views.check_domain_policy_dns",
                        return_value=(True, "Verified.")), mock.patch(
            "apps.transport_security.views.check_publication_txt",
            return_value=(None, True),
        ):
            response = self.client.post(self.base + "verify-dns/", {}, format="json")
        self.assertEqual(response.status_code, 503)
        row.refresh_from_db()
        self.assertEqual(row.lifecycle, TransportSecurityLifecycle.ACTIVE)
        self.assertEqual(row.sts_txt_verified_at, checked)
        self.assertEqual(row.dns_records_checked_at, checked)

    def test_publication_check_is_tenant_scoped(self):
        alien = make_user("dns-phase2-alien@tenant.example")
        other = make_tenant(alien, "Another security tenant", "another-sec-tenant")
        client = auth_client(alien, other)
        self.assertEqual(client.post(self.base + "verify-dns/", {}, format="json").status_code, 404)

    def test_exact_txt_match_and_dns_failures(self):
        from apps.transport_security.dns import _exact_txt
        from types import SimpleNamespace
        import dns
        with mock.patch("apps.transport_security.dns.dns.resolver.resolve",
                        return_value=[SimpleNamespace(strings=[b"v=STSv1; ", b"id=test"])]):
            self.assertTrue(_exact_txt("_mta-sts.company.example", "v=STSv1; id=test"))
            self.assertFalse(_exact_txt("_mta-sts.company.example", "v=STSv1; id=other"))
        with mock.patch("apps.transport_security.dns.dns.resolver.resolve",
                        side_effect=dns.resolver.NXDOMAIN):
            self.assertFalse(_exact_txt("_mta-sts.company.example", "v=STSv1; id=test"))
        with mock.patch("apps.transport_security.dns.dns.resolver.resolve",
                        side_effect=dns.exception.Timeout):
            self.assertIsNone(_exact_txt("_mta-sts.company.example", "v=STSv1; id=test"))

    def test_retirement_worker_requires_exact_secret_and_domain_identity(self):
        self.client.post(self.base, {"enabled": True}, format="json")
        row = DomainTransportSecurity.objects.get(domain=self.domain)
        row.lifecycle = TransportSecurityLifecycle.READY
        row.save(update_fields=["lifecycle"])
        requested = self.client.post(self.base, {"enabled": False}, format="json")
        self.assertEqual(requested.status_code, 200)
        url = self.internal + "retirement/pending/"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, HTTP_X_MATEMAIL_TRANSPORT_SECRET="wrong").status_code, 403)
        pending = self.client.get(url, **self.header)
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(len(pending.data["results"]), 1)
        job = pending.data["results"][0]
        self.assertEqual(job["id"], str(self.domain.id))
        self.assertEqual(job["tenant_id"], str(self.tenant.id))
        self.assertEqual(job["lifecycle"], "deactivating")
        self.assertFalse(job["cleanup_ready"])
        self.assertEqual(
            self.client.post(self.internal + "retirement/authorize/", {
                "id": str(self.domain.id), "policy_id": "f" * 24, "action": "serve-none",
            }, format="json", **self.header).status_code, 404,
        )
        self.assertEqual(self.client.post(self.internal + "retirement/authorize/", {
            "id": str(self.domain.id), "policy_id": row.policy_id, "action": "serve-none",
        }, format="json", **self.header).status_code, 200)

    def test_retirement_cache_drain_requires_48_hours_of_dns_absence(self):
        from datetime import timedelta
        from django.utils import timezone
        self.client.post(self.base, {"enabled": True}, format="json")
        row = DomainTransportSecurity.objects.get(domain=self.domain)
        row.lifecycle = TransportSecurityLifecycle.ACTIVE
        row.save(update_fields=["lifecycle"])
        self.client.post(self.base, {"enabled": False}, format="json")
        state_url = self.internal + f"retirement/{self.domain.pk}/state/"
        def post(action, **kwargs):
            return self.client.post(state_url, {
                "action": action, "policy_id": row.policy_id, **kwargs,
            }, format="json", **self.header)
        self.assertEqual(post("complete").status_code, 409)
        self.assertEqual(post("mode-none").status_code, 200)
        self.assertEqual(post("mode-none").status_code, 409)
        self.assertEqual(post("dns-check", absent=True).status_code, 200)
        self.assertEqual(post("complete").status_code, 409)
        row.refresh_from_db()
        self.assertEqual(row.lifecycle, "draining")
        self.assertTrue(row.enabled)
        self.assertIsNotNone(row.deactivation_dns_absent_since)
        first_absent = row.deactivation_dns_absent_since
        self.assertEqual(post("dns-check", absent=True).status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.deactivation_dns_absent_since, first_absent)
        self.assertEqual(post("dns-check", absent=False).status_code, 200)
        row.refresh_from_db()
        self.assertIsNone(row.deactivation_dns_absent_since)
        self.assertEqual(post("dns-check", absent=True).status_code, 200)
        row.refresh_from_db()
        row.deactivation_dns_absent_since = timezone.now() - timedelta(hours=49)
        row.deactivation_policy_none_at = timezone.now() - timedelta(hours=49)
        row.save(update_fields=["deactivation_dns_absent_since", "deactivation_policy_none_at"])
        auth = self.client.post(self.internal + "retirement/authorize/", {
            "id": str(self.domain.id), "policy_id": row.policy_id, "action": "cleanup",
        }, format="json", **self.header)
        self.assertEqual(auth.status_code, 200)
        self.assertTrue(auth.data["cleanup_ready"])
        self.assertEqual(post("complete").status_code, 200)
        row.refresh_from_db()
        self.assertFalse(row.enabled)
        self.assertEqual(row.lifecycle, TransportSecurityLifecycle.DISABLED)
        self.assertIsNotNone(row.deactivation_completed_at)

    def test_retirement_rejects_forged_state_and_cross_tenant_writes(self):
        self.client.post(self.base, {"enabled": True}, format="json")
        row = DomainTransportSecurity.objects.get(domain=self.domain)
        row.lifecycle = TransportSecurityLifecycle.READY
        row.save(update_fields=["lifecycle"])
        self.client.post(self.base, {"enabled": False}, format="json")
        endpoint = self.internal + f"retirement/{self.domain.pk}/state/"
        self.assertEqual(self.client.post(endpoint, {
            "action": "mode-none", "policy_id": row.policy_id,
        }, format="json").status_code, 403)
        self.assertEqual(self.client.post(endpoint, {
            "action": "mode-none", "policy_id": "f"*24,
        }, format="json", **self.header).status_code, 409)
        self.assertEqual(self.client.post(endpoint, {
            "action": "dns-check", "policy_id": row.policy_id, "absent": "true",
        }, format="json", **self.header).status_code, 400)
        other_user = make_user("retirement-other@tenant.example")
        other_tenant = make_tenant(other_user, "Other Retirement", "retire-other")
        other_client = auth_client(other_user, other_tenant)
        self.assertEqual(other_client.get(self.base).status_code, 404)
        self.assertEqual(other_client.post(self.base, {"enabled": False}, format="json").status_code, 404)

    def test_policy_hostname_must_fit_dns_limit(self):
        with self.assertRaises(ValueError):
            policy_hostname("a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 60 + ".com")
