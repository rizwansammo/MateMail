"""Phase D migration acceptance: keep existing tenants alive while DNS shifts to .pro."""
from unittest import mock

from django.test import TestCase, override_settings

from apps.dnshealth.models import DNSCheckStatus, DNSRecordCheck
from apps.dnshealth.services import check_dns_for_domain
from apps.domains.models import DomainStatus
from apps.tenants.custom_hosts import check_custom_hostname_dns
from tests.factories import make_domain, make_tenant, make_user


MIGRATION = {
    "MAIL_HOSTNAME": "mx.matemail.pro",
    "LEGACY_MAIL_HOSTNAME": "mx.matemail.online",
    "SPF_INCLUDE_DOMAIN": "_spf.matemail.pro",
    "LEGACY_SPF_INCLUDE_DOMAIN": "_spf.matemail.online",
    "AUTODISCOVER_HOST": "autodiscover.matemail.pro",
    "LEGACY_AUTODISCOVER_HOST": "autodiscover.matemail.online",
}


@override_settings(**MIGRATION)
class DNSMigrationAcceptanceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.domain = make_domain(make_tenant(make_user("owner@migration.example")), "migration.example")
        cls.domain.dkim_public_key = "PUBLICKEY"
        cls.domain.save(update_fields=["dkim_public_key"])

    def _verify(self, mx, spf, srv):
        def lookup_txt(host):
            if host.startswith("_dmarc."):
                return ["v=DMARC1; p=none; rua=mailto:test@example.com"]
            if "._domainkey." in host:
                return ["v=DKIM1; k=rsa; p=PUBLICKEY"]
            return spf
        with mock.patch("apps.dnshealth.services._resolve_mx", return_value=mx), \
             mock.patch("apps.dnshealth.services._resolve_txt", side_effect=lookup_txt), \
             mock.patch("apps.dnshealth.services._resolve_srv", return_value=srv):
            self.domain.refresh_from_db()
            return check_dns_for_domain(self.domain)

    def test_legacy_customer_stays_active_and_receives_new_instructions(self):
        result = self._verify(
            ["10 mx.matemail.online"], ["v=spf1 include:_spf.matemail.online ~all"],
            ["0 0 443 autodiscover.matemail.online."],
        )
        self.assertEqual(DomainStatus.ACTIVE, result.status)
        self.assertEqual(100, result.dns_health_score)
        mx = DNSRecordCheck.objects.get(domain=result, record_type="MX")
        self.assertEqual("10 mx.matemail.pro", mx.expected_value)
        self.assertEqual("10 mx.matemail.online", mx.detected_value)
        spf = DNSRecordCheck.objects.get(domain=result, record_type="TXT", host=result.domain)
        self.assertIn("_spf.matemail.pro", spf.expected_value)
        self.assertIn("_spf.matemail.online", spf.detected_value)

    def test_new_customer_records_verify(self):
        result = self._verify(
            ["10 mx.matemail.pro"], ["v=spf1 include:_spf.matemail.pro ~all"],
            ["0 0 443 autodiscover.matemail.pro."],
        )
        self.assertEqual(DomainStatus.ACTIVE, result.status)
        self.assertEqual(100, result.dns_health_score)

    def test_duplicate_spf_records_are_rejected(self):
        result = self._verify(
            ["10 mx.matemail.pro"],
            ["v=spf1 include:_spf.matemail.pro ~all", "v=spf1 include:_spf.matemail.online ~all"],
            [],
        )
        spf = DNSRecordCheck.objects.get(domain=result, record_type="TXT", host=result.domain)
        self.assertEqual(DNSCheckStatus.FAILED, spf.status)
        self.assertNotEqual(DomainStatus.ACTIVE, result.status)


@override_settings(
    CUSTOM_HOST_CNAME_TARGET="custom.matemail.pro",
    CUSTOM_HOST_LEGACY_CNAME_TARGETS=("custom.matemail.online",),
)
class CustomHostnameMigrationAcceptanceTest(TestCase):
    def test_existing_custom_hostname_stays_verified(self):
        with mock.patch(
            "apps.tenants.custom_hosts._lookup_cname_targets",
            return_value=(["custom.matemail.online"], ""),
        ):
            verified, message, technical = check_custom_hostname_dns("postbox.example.com")
        self.assertTrue(verified)
        self.assertIn("legacy", message.lower())
        self.assertEqual("", technical)

    def test_new_custom_hostname_verifies(self):
        with mock.patch(
            "apps.tenants.custom_hosts._lookup_cname_targets",
            return_value=(["custom.matemail.pro"], ""),
        ):
            verified, _, _ = check_custom_hostname_dns("postbox.example.com")
        self.assertTrue(verified)
