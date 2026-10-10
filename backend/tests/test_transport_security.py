"""P4-C.B: multi-tenant optional transport-security API, with NO network/edge calls."""
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.tenants.models import MemberRole, TenantStatus
from apps.transport_security.models import (
    DomainTransportSecurity, TransportSecurityLifecycle,
    TransportSecurityCertificateStatus,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS, add_member, auth_client, disable_throttling,
    make_domain, make_tenant, make_unapproved_tenant, make_unverified_domain,
    make_user,
)


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=True,
    MTA_STS_POLICY_EDGE_TARGET="",
    TLS_RPT_REPORT_ADDRESS="tlsrpt@mail.matemail.pro",
)
class TransportSecurityAPITest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner_a = make_user("ts-owner-a@example.test")
        self.tenant_a = make_tenant(self.owner_a, "Transport A", "ts-owner-a")
        self.domain_a = make_domain(self.tenant_a, "company.example")
        self.client_a = auth_client(self.owner_a, self.tenant_a)
        self.owner_b = make_user("ts-owner-b@example.test")
        self.tenant_b = make_tenant(self.owner_b, "Transport B", "ts-owner-b")
        self.domain_b = make_domain(self.tenant_b, "elsewhere.example")
        self.client_b = auth_client(self.owner_b, self.tenant_b)
        self.url_a = f"/api/domains/{self.domain_a.pk}/transport-security/"

    def test_default_is_optional_and_get_is_read_only(self):
        reply = self.client_a.get(self.url_a)
        self.assertEqual(reply.status_code, 200)
        self.assertFalse(reply.data["enabled"])
        self.assertTrue(reply.data["optional"])
        self.assertTrue(reply.data["self_service_available"])
        self.assertEqual(reply.data["lifecycle"], "disabled")
        self.assertEqual(reply.data["dns_records"], [])
        self.assertEqual(reply.data["policy_mode"], "testing")
        self.assertFalse(reply.data["can_publish_dns"])
        self.assertEqual(DomainTransportSecurity.objects.count(), 0)

    def test_admin_opt_in_generates_safe_non_publishable_fqdn_records(self):
        result = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data["enabled"])
        self.assertEqual(result.data["lifecycle"], "pending_dns")
        self.assertEqual(result.data["policy_mode"], "testing")
        self.assertEqual(result.data["mx"], "mx.matemail.pro")
        self.assertEqual(result.data["max_age_seconds"], 86400)
        self.assertFalse(result.data["edge_configured"])
        self.assertFalse(result.data["can_publish_dns"])
        records = result.data["dns_records"]
        self.assertEqual([r["type"] for r in records], ["CNAME", "TXT", "TXT"])
        self.assertEqual([r["host"] for r in records], [
            "mta-sts.company.example",
            "_mta-sts.company.example",
            "_smtp._tls.company.example",
        ])
        self.assertIsNone(records[0]["value"])
        self.assertTrue(records[1]["value"].startswith("v=STSv1; id="))
        self.assertEqual(
            records[2]["value"],
            "v=TLSRPTv1; rua=mailto:tlsrpt@mail.matemail.pro",
        )
        self.assertTrue(all(r["publish_ready"] is False for r in records))
        self.assertEqual(DomainTransportSecurity.objects.count(), 1)
        self.domain_a.refresh_from_db()
        self.assertFalse(self.domain_a.mail_engine_provisioned)
        self.assertEqual(self.domain_a.dns_health_score, 0)

    def test_repeated_enable_does_not_rotate_policy(self):
        a = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        b = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        self.assertEqual(
            a.data["dns_records"][1]["value"],
            b.data["dns_records"][1]["value"],
        )
        self.assertEqual(DomainTransportSecurity.objects.count(), 1)

    def test_opt_out_then_opt_in_rotates_version(self):
        a = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        disabled = self.client_a.post(self.url_a, {"enabled": False}, format="json")
        self.assertFalse(disabled.data["enabled"])
        self.assertEqual(disabled.data["dns_records"], [])
        b = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        self.assertNotEqual(
            a.data["dns_records"][1]["value"],
            b.data["dns_records"][1]["value"],
        )

    def test_disabling_untouched_domain_is_idempotent_and_no_write(self):
        res = self.client_a.post(self.url_a, {"enabled": False}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(DomainTransportSecurity.objects.count(), 0)

    def test_active_policy_starts_staged_retirement_without_disabling_hosting(self):
        self.client_a.post(self.url_a, {"enabled": True}, format="json")
        row = DomainTransportSecurity.objects.get(domain=self.domain_a)
        row.lifecycle = TransportSecurityLifecycle.ACTIVE
        row.save(update_fields=["lifecycle"])
        res = self.client_a.post(self.url_a, {"enabled": False}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["enabled"])
        self.assertTrue(res.data["offboarding"])
        self.assertEqual(res.data["lifecycle"], "deactivating")
        self.assertEqual(len(res.data["offboarding_dns_records"]), 2)
        row.refresh_from_db()
        self.assertIsNotNone(row.deactivation_requested_at)
        self.assertTrue(row.enabled)
        self.assertEqual(row.lifecycle, TransportSecurityLifecycle.DEACTIVATING)

        # Repeated disable calls cannot reset the waiting period.
        first = row.deactivation_requested_at
        again = self.client_a.post(self.url_a, {"enabled": False}, format="json")
        self.assertEqual(again.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.deactivation_requested_at, first)
        self.assertEqual(
            self.client_a.post(self.url_a, {"enabled": True}, format="json").status_code, 409,
        )

    def test_retirement_can_begin_after_partial_provisioning_error(self):
        self.client_a.post(self.url_a, {"enabled": True}, format="json")
        row = DomainTransportSecurity.objects.get(domain=self.domain_a)
        for stage in (TransportSecurityLifecycle.PROVISIONING,
                      TransportSecurityLifecycle.READY, TransportSecurityLifecycle.ERROR):
            row.lifecycle = stage
            row.save(update_fields=["lifecycle"])
            reply = self.client_a.post(self.url_a, {"enabled": False}, format="json")
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(reply.data["lifecycle"], "deactivating")
            row.refresh_from_db()

    def test_retirement_does_not_require_current_dns_ownership_to_cancel(self):
        from apps.domains.models import DomainOwnership
        self.client_a.post(self.url_a, {"enabled": True}, format="json")
        row = DomainTransportSecurity.objects.get(domain=self.domain_a)
        row.lifecycle = TransportSecurityLifecycle.ACTIVE
        row.save(update_fields=["lifecycle"])
        self.domain_a.ownership_status = DomainOwnership.PENDING
        self.domain_a.save(update_fields=["ownership_status"])
        result = self.client_a.post(self.url_a, {"enabled": False}, format="json")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["lifecycle"], "deactivating")

    def test_non_owner_cannot_see_or_mutate_another_tenants_domain(self):
        self.assertEqual(self.client_b.get(self.url_a).status_code, 404)
        res = self.client_b.post(self.url_a, {"enabled": True}, format="json")
        self.assertEqual(res.status_code, 404)
        self.assertFalse(DomainTransportSecurity.objects.exists())

    def test_read_only_member_cannot_enable_but_can_read(self):
        reader = make_user("ts-reader@example.test")
        add_member(self.tenant_a, reader, MemberRole.READ_ONLY)
        client = auth_client(reader, self.tenant_a)
        self.assertEqual(client.get(self.url_a).status_code, 200)
        self.assertEqual(
            client.post(self.url_a, {"enabled": True}, format="json").status_code,
            403,
        )

    def test_unverified_user_cannot_mutate(self):
        user = make_user("ts-unverified-user@example.test", verified=False)
        add_member(self.tenant_a, user, MemberRole.ADMIN)
        client = auth_client(user, self.tenant_a)
        self.assertEqual(
            client.post(self.url_a, {"enabled": True}, format="json").status_code,
            403,
        )

    def test_unverified_domain_is_not_eligible(self):
        pending = make_unverified_domain(self.tenant_a, "pending.example")
        res = self.client_a.post(
            f"/api/domains/{pending.pk}/transport-security/",
            {"enabled": True}, format="json",
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(DomainTransportSecurity.objects.count(), 0)

    def test_suspended_tenant_cannot_enable(self):
        self.tenant_a.status = TenantStatus.SUSPENDED
        self.tenant_a.save(update_fields=["status"])
        res = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(DomainTransportSecurity.objects.count(), 0)

    def test_only_enabled_field_is_writable(self):
        for invalid in (
            {},
            {"enabled": True, "policy_mode": "enforce"},
            {"enabled": True, "tenant": str(self.tenant_b.id)},
            {"enabled": True, "lifecycle": "active"},
            {"enabled": True, "policy_id": "attacker"},
            {"enabled": True, "mx": "attacker.example"},
        ):
            with self.subTest(payload=invalid):
                response = self.client_a.post(self.url_a, invalid, format="json")
                self.assertEqual(response.status_code, 400)
        self.assertFalse(DomainTransportSecurity.objects.exists())

    def test_does_not_modify_dns_engine_or_custom_hostname(self):
        with mock.patch("dns.resolver.resolve") as resolve, mock.patch(
            "apps.dnshealth.tasks.check_domain_dns.delay"
        ) as queued:
            response = self.client_a.post(
                self.url_a, {"enabled": True}, format="json",
            )
            self.assertEqual(response.status_code, 200)
        resolve.assert_not_called()
        queued.assert_not_called()

    @override_settings(MTA_STS_POLICY_EDGE_TARGET="mta-sts-gateway.matemail.pro")
    def test_configured_edge_is_still_not_claimed_as_https_ready(self):
        res = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        self.assertTrue(res.data["edge_configured"])
        self.assertEqual(
            res.data["dns_records"][0]["value"], "mta-sts-gateway.matemail.pro",
        )
        self.assertFalse(res.data["can_publish_dns"])
        self.assertFalse(res.data["dns_records"][0]["publish_ready"])

    def test_secondary_domain_has_separate_policy_id_and_scoped_record(self):
        self.client_a.post(self.url_a, {"enabled": True}, format="json")
        url_b = f"/api/domains/{self.domain_b.pk}/transport-security/"
        b = self.client_b.post(url_b, {"enabled": True}, format="json")
        self.assertEqual(b.status_code, 200)
        self.assertEqual(b.data["dns_records"][0]["host"], "mta-sts.elsewhere.example")
        self.assertEqual(DomainTransportSecurity.objects.count(), 2)

    def test_anonymous_request_is_rejected(self):
        client = APIClient()
        self.assertIn(client.get(self.url_a).status_code, [401, 403])
        self.assertFalse(DomainTransportSecurity.objects.exists())

    @override_settings(
        MTA_STS_POLICY_EDGE_TARGET="mta-sts-gateway.matemail.pro",
        MTA_STS_POLICY_EDGE_READY=True,
    )
    def test_domain_ownership_loss_blocks_cname_and_sts_txt(self):
        from apps.domains.models import DomainOwnership
        response = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        self.assertEqual(response.status_code, 200)
        row = DomainTransportSecurity.objects.get(domain=self.domain_a)
        row.lifecycle = TransportSecurityLifecycle.READY
        row.certificate_status = TransportSecurityCertificateStatus.ACTIVE
        row.cert_verified_at = timezone.now()
        row.save(update_fields=["lifecycle", "certificate_status", "cert_verified_at"])
        ready = self.client_a.get(self.url_a)
        self.assertTrue(ready.data["dns_records"][0]["publish_ready"])
        self.assertTrue(ready.data["dns_records"][1]["publish_ready"])
        self.domain_a.ownership_status = DomainOwnership.PENDING
        self.domain_a.save(update_fields=["ownership_status"])
        withdrawn = self.client_a.get(self.url_a)
        self.assertEqual(withdrawn.status_code, 200)
        self.assertFalse(withdrawn.data["ownership_verified"])
        self.assertFalse(any(record["publish_ready"] for record in withdrawn.data["dns_records"]))
        self.assertFalse(withdrawn.data["can_publish_dns"])


    @override_settings(
        TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=False,
    )
    def test_canary_allowlist_only_grants_exact_domain_uuid(self):
        from django.test import override_settings
        from apps.transport_security.serializers import self_service_available_for

        with override_settings(TRANSPORT_SECURITY_CANARY_DOMAIN_IDS=str(self.domain_a.pk)):
            first = self.client_a.get(self.url_a)
            self.assertEqual(first.status_code, 200)
            self.assertTrue(first.data["self_service_available"])

            # Another tenant is not affected; its known UUID is unapproved.
            url_b = f"/api/domains/{self.domain_b.pk}/transport-security/"
            self.assertFalse(self.client_b.get(url_b).data["self_service_available"])
            self.assertEqual(
                self.client_b.post(url_b, {"enabled": True}, format="json").status_code,
                503,
            )
            self.assertEqual(self.client_a.post(
                self.url_a, {"enabled": True}, format="json",
            ).status_code, 200)
            self.assertEqual(self.client_b.get(self.url_a).status_code, 404)

        for invalid in (
            str(self.tenant_a.id), self.domain_a.domain, "*",
            str(self.domain_a.pk)[:-1], "", "untrusted",
        ):
            with self.subTest(invalid=invalid), override_settings(
                TRANSPORT_SECURITY_CANARY_DOMAIN_IDS=invalid,
            ):
                self.assertFalse(self_service_available_for(self.domain_a))
                self.assertFalse(self.client_a.get(self.url_a).data["self_service_available"])

    @override_settings(TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=False)
    def test_canary_does_not_enable_global_self_service_by_default(self):
        from django.test import override_settings
        url_b = f"/api/domains/{self.domain_b.pk}/transport-security/"
        with override_settings(TRANSPORT_SECURITY_CANARY_DOMAIN_IDS=""):
            self.assertEqual(
                self.client_a.post(self.url_a, {"enabled": True}, format="json").status_code, 503,
            )
            self.assertEqual(
                self.client_b.post(url_b, {"enabled": True}, format="json").status_code, 503,
            )

    @override_settings(TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=False)
    def test_release_default_is_fail_closed(self):
        res = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        self.assertEqual(res.status_code, 503)
        self.assertFalse(DomainTransportSecurity.objects.exists())
        response = self.client_a.get(self.url_a)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["self_service_available"])
