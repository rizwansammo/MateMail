"""
The provider SPF include, and keeping Autodiscover out of the health score.

TWO SEPARATE INVARIANTS

    1. Customer SPF says `include:_spf.matemail.pro`, never
       `include:matemail.online`. The old form made the product's website
       domain double as the provider's SPF authorisation record, so every
       customer's ability to send depended on a TXT record living on a domain
       that exists for marketing (DEC-056).

    2. The Autodiscover SRV record is checked and stored, but never scored. A
       domain without it sends and receives mail perfectly; counting it would
       report a healthy domain as 80% and send somebody hunting a fault that
       does not exist.
"""
from unittest import mock

from django.test import TestCase, override_settings

from apps.dnshealth.models import DNSCheckStatus, DNSRecordCheck
from apps.dnshealth.services import _discovery_records, _expected_records, check_dns_for_domain
from apps.domains.models import DomainStatus
from tests.factories import make_domain, make_tenant, make_user


def _label(records, label):
    for record in records:
        if record["label"] == label:
            return record
    raise AssertionError(f"no {label} record")


@override_settings(MAIL_DOMAIN="matemail.pro", MAIL_HOSTNAME="mx.matemail.pro", SPF_INCLUDE_DOMAIN="_spf.matemail.pro", AUTODISCOVER_HOST="autodiscover.matemail.pro")
class SpfIncludeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = make_tenant(make_user("owner@acme.example"))
        cls.domain = make_domain(cls.tenant, "customer.example")

    def test_customer_spf_includes_the_provider_host(self):
        spf = _label(_expected_records(self.domain), "SPF")
        self.assertEqual(
            "v=spf1 include:_spf.matemail.pro ~all", spf["expected_value"]
        )
        self.assertEqual("include:_spf.matemail.pro", spf["match_contains"])

    def test_customer_spf_never_includes_the_website_domain(self):
        """
        The regression. `include:matemail.online` is what this replaced, and
        the failure mode of a silent revert is invisible: SPF still passes
        while the product domain carries authorisation it should not.
        """
        spf = _label(_expected_records(self.domain), "SPF")
        self.assertNotIn("include:matemail.online", spf["expected_value"])
        self.assertNotIn("include:matemail.online", spf["match_contains"])

    @override_settings(SPF_INCLUDE_DOMAIN="_spf.example.test")
    def test_the_include_host_is_configuration(self):
        spf = _label(_expected_records(self.domain), "SPF")
        self.assertEqual("v=spf1 include:_spf.example.test ~all", spf["expected_value"])

    def test_dmarc_reports_to_a_real_native_receive_mailbox(self):
        """
        Product apex matemail.pro has no MX. Reporting to dmarc@matemail.pro
        silently loses reports. E4 provisioned an actual receiving-only
        mailbox under mail.matemail.pro, whose MX is published.
        """
        dmarc = _label(_expected_records(self.domain), "DMARC")
        self.assertIn(
            "rua=mailto:dmarc@mail.matemail.pro", dmarc["expected_value"]
        )

    def test_mx_is_unchanged(self):
        mx = _label(_expected_records(self.domain), "MX")
        self.assertEqual("10 mx.matemail.pro", mx["expected_value"])


@override_settings(MAIL_DOMAIN="matemail.pro", MAIL_HOSTNAME="mx.matemail.pro", SPF_INCLUDE_DOMAIN="_spf.matemail.pro", AUTODISCOVER_HOST="autodiscover.matemail.pro")
class DiscoveryRecordTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = make_tenant(make_user("owner@acme.example"))
        cls.domain = make_domain(cls.tenant, "customer.example")

    def test_the_srv_record_points_at_the_central_host(self):
        srv = _label(_discovery_records(self.domain), "AUTODISCOVER")
        self.assertEqual("SRV", srv["record_type"])
        self.assertEqual("_autodiscover._tcp.customer.example", srv["host"])
        self.assertEqual("0 0 443 autodiscover.matemail.pro.", srv["expected_value"])

    @override_settings(AUTODISCOVER_HOST="autodiscover.example.test")
    def test_the_target_is_configuration(self):
        srv = _label(_discovery_records(self.domain), "AUTODISCOVER")
        self.assertEqual("0 0 443 autodiscover.example.test.", srv["expected_value"])

    def test_the_scored_records_are_still_exactly_the_four_mail_records(self):
        """
        The score is `verified * 25`. A fifth scored record would make a
        perfect domain report 125, and a missing one would drag a working
        domain below the threshold the UI colours green.
        """
        labels = {record["label"] for record in _expected_records(self.domain)}
        self.assertEqual({"MX", "SPF", "DKIM", "DMARC"}, labels)


def _no_dns(*_args, **_kwargs):
    """Every lookup fails, so nothing is verified and the score is 0."""
    raise Exception("no resolver in tests")


@override_settings(MAIL_DOMAIN="matemail.pro", MAIL_HOSTNAME="mx.matemail.pro", SPF_INCLUDE_DOMAIN="_spf.matemail.pro", AUTODISCOVER_HOST="autodiscover.matemail.pro")
class ScoreIsolationTest(TestCase):
    """Autodiscover must not be able to move the mail health score."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = make_tenant(make_user("owner@acme.example"))

    def _check_with(self, mx, txt, srv):
        domain = make_domain(self.tenant, "score.example", status=DomainStatus.PENDING)
        domain.dkim_public_key = "PUBKEY"
        domain.save(update_fields=["dkim_public_key"])

        with mock.patch("apps.dnshealth.services._resolve_mx", return_value=mx), \
             mock.patch("apps.dnshealth.services._resolve_txt", side_effect=txt), \
             mock.patch("apps.dnshealth.services._resolve_srv", return_value=srv):
            return check_dns_for_domain(domain)

    @staticmethod
    def _all_mail_records_present(host):
        if host.startswith("_dmarc"):
            return ["v=DMARC1; p=none;"]
        if "._domainkey." in host:
            return ["v=DKIM1; k=rsa; p=PUBKEY"]
        return ["v=spf1 include:_spf.matemail.pro ~all"]

    def test_a_missing_srv_record_still_scores_one_hundred(self):
        domain = self._check_with(
            mx=["10 mx.matemail.pro"],
            txt=self._all_mail_records_present,
            srv=[],
        )
        self.assertEqual(100, domain.dns_health_score)
        self.assertEqual(DomainStatus.ACTIVE, domain.status)

    def test_a_present_srv_record_does_not_push_the_score_past_one_hundred(self):
        domain = self._check_with(
            mx=["10 mx.matemail.pro"],
            txt=self._all_mail_records_present,
            srv=["0 0 443 autodiscover.matemail.pro."],
        )
        self.assertEqual(100, domain.dns_health_score)

    def test_a_present_srv_record_cannot_rescue_a_broken_domain(self):
        """
        The inverse mistake: discovery counting toward health would let a
        domain with no MX report as partly working.
        """
        domain = self._check_with(
            mx=[],
            txt=lambda host: [],
            srv=["0 0 443 autodiscover.matemail.pro."],
        )
        self.assertEqual(0, domain.dns_health_score)
        self.assertEqual(DomainStatus.PENDING, domain.status)

    def test_the_srv_result_is_recorded_and_flagged_unscored(self):
        domain = self._check_with(
            mx=["10 mx.matemail.pro"],
            txt=self._all_mail_records_present,
            srv=["0 0 443 autodiscover.matemail.pro."],
        )
        srv = DNSRecordCheck.objects.get(domain=domain, record_type="SRV")
        self.assertEqual(DNSCheckStatus.VERIFIED, srv.status)
        self.assertFalse(srv.is_scored)

        # And every mail record is scored, so the flag is not simply False.
        self.assertEqual(
            4, DNSRecordCheck.objects.filter(domain=domain, is_scored=True).count()
        )

    def test_an_srv_record_aimed_at_the_wrong_port_is_not_verified(self):
        """
        A record with the right target on the wrong port sends Outlook to
        somewhere nothing is listening — worse than no record, because the
        client stops looking.
        """
        domain = self._check_with(
            mx=["10 mx.matemail.pro"],
            txt=self._all_mail_records_present,
            srv=["0 0 80 autodiscover.matemail.pro."],
        )
        srv = DNSRecordCheck.objects.get(domain=domain, record_type="SRV")
        self.assertEqual(DNSCheckStatus.FAILED, srv.status)
        self.assertEqual(100, domain.dns_health_score)

    def test_a_missing_srv_record_does_not_block_anything(self):
        """
        §16: missing discovery must not make a domain inactive or block
        provisioning. ACTIVE with a missing SRV is the proof.
        """
        domain = self._check_with(
            mx=["10 mx.matemail.pro"],
            txt=self._all_mail_records_present,
            srv=[],
        )
        self.assertEqual(DomainStatus.ACTIVE, domain.status)
        srv = DNSRecordCheck.objects.get(domain=domain, record_type="SRV")
        self.assertEqual(DNSCheckStatus.MISSING, srv.status)
