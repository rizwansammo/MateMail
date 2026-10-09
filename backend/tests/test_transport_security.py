"""P4-C.B: multi-tenant optional transport-security API, with NO network/edge calls."""
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.tenants.models import MemberRole, TenantStatus
from apps.transport_security.models import (
    DomainTransportSecurity, TransportSecurityLifecycle,
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

    def test_active_policy_cannot_be_disabled_without_edge_deactivation(self):
        self.client_a.post(self.url_a, {"enabled": True}, format="json")
        row = DomainTransportSecurity.objects.get(domain=self.domain_a)
        row.lifecycle = TransportSecurityLifecycle.ACTIVE
        row.save(update_fields=["lifecycle"])
        denied = self.client_a.post(self.url_a, {"enabled": False}, format="json")
        self.assertEqual(denied.status_code, 409)
        row.refresh_from_db()
        self.assertTrue(row.enabled)

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

    @override_settings(TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=False)
    def test_release_default_is_fail_closed(self):
        res = self.client_a.post(self.url_a, {"enabled": True}, format="json")
        self.assertEqual(res.status_code, 503)
        self.assertFalse(DomainTransportSecurity.objects.exists())
        response = self.client_a.get(self.url_a)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["self_service_available"])
