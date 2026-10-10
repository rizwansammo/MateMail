"""Regression tests for stale Docker DNS NXDOMAIN after public DNS publication.

Public records in two independent resolvers must never be represented as
"Missing" simply because 127.0.0.11 returned an older negative cache entry.
"""
from types import SimpleNamespace
from unittest import mock

import dns.exception
import dns.resolver
from django.test import TestCase

from apps.dnshealth.models import DNSRecordCheck
from apps.dnshealth.services import (
    DNSLookupInconclusive, _resolve_mx, _resolve_txt, check_dns_for_domain,
)
from tests.factories import make_domain, make_tenant, make_user


class _MX:
    preference = 10
    exchange = "mx.matemail.pro."

    def __str__(self):
        return "10 mx.matemail.pro."


class _TXT:
    strings = [b"v=DMARC1; p=none"]

    def __str__(self):
        return '"v=DMARC1; p=none"'


def _public_answer(records):
    return SimpleNamespace(resolve=mock.Mock(side_effect=records))


class PublicDNSCorroborationTests(TestCase):
    def test_default_nxdomain_but_both_public_resolvers_find_mx(self):
        fake = _public_answer([[ _MX() ], [ _MX() ]])
        with mock.patch("apps.dnshealth.services.dns.resolver.resolve",
                        side_effect=dns.resolver.NXDOMAIN), mock.patch(
            "apps.dnshealth.services.dns.resolver.Resolver", return_value=fake,
        ):
            self.assertEqual(_resolve_mx("dns-canary.netamate.com"), ["10 mx.matemail.pro"])
        self.assertEqual(fake.resolve.call_count, 2)

    def test_default_nxdomain_but_both_public_resolvers_find_dmarc(self):
        fake = _public_answer([[ _TXT() ], [ _TXT() ]])
        with mock.patch("apps.dnshealth.services.dns.resolver.resolve",
                        side_effect=dns.resolver.NXDOMAIN), mock.patch(
            "apps.dnshealth.services.dns.resolver.Resolver", return_value=fake,
        ):
            self.assertEqual(_resolve_txt("_dmarc.dns-canary.netamate.com"),
                             ["v=DMARC1; p=none"])

    def test_valid_default_answer_requires_no_extra_public_dns(self):
        with mock.patch("apps.dnshealth.services.dns.resolver.resolve",
                        return_value=[_MX()]), mock.patch(
            "apps.dnshealth.services.dns.resolver.Resolver",
        ) as fallback:
            self.assertEqual(_resolve_mx("example.com"), ["10 mx.matemail.pro"])
        fallback.assert_not_called()

    def test_both_public_resolvers_agree_nxdomain_is_really_missing(self):
        fake = _public_answer([dns.resolver.NXDOMAIN(), dns.resolver.NXDOMAIN()])
        with mock.patch("apps.dnshealth.services.dns.resolver.resolve",
                        side_effect=dns.resolver.NXDOMAIN), mock.patch(
            "apps.dnshealth.services.dns.resolver.Resolver", return_value=fake,
        ):
            self.assertEqual(_resolve_txt("_dmarc.missing.example"), [])

    def test_one_public_resolver_disagrees_does_not_mark_missing(self):
        fake = _public_answer([[ _MX() ], dns.resolver.NXDOMAIN()])
        with mock.patch("apps.dnshealth.services.dns.resolver.resolve",
                        side_effect=dns.resolver.NXDOMAIN), mock.patch(
            "apps.dnshealth.services.dns.resolver.Resolver", return_value=fake,
        ):
            with self.assertRaises(DNSLookupInconclusive):
                _resolve_mx("customer.example")

    def test_public_resolver_timeout_does_not_mark_missing(self):
        fake = _public_answer([dns.exception.Timeout()])
        with mock.patch("apps.dnshealth.services.dns.resolver.resolve",
                        side_effect=dns.exception.Timeout), mock.patch(
            "apps.dnshealth.services.dns.resolver.Resolver", return_value=fake,
        ):
            with self.assertRaises(DNSLookupInconclusive):
                _resolve_txt("_dmarc.customer.example")


class HealthSnapshotTests(TestCase):
    def setUp(self):
        self.domain = make_domain(
            make_tenant(make_user("stale-resolver@tenant.example")),
            "stale-resolver.example",
        )
        self.domain.dkim_public_key = "SAMPLE_PUBLIC"
        self.domain.save(update_fields=["dkim_public_key"])

    @staticmethod
    def valid_txt(hostname):
        if hostname.startswith("_dmarc"):
            return ["v=DMARC1; p=none"]
        if "._domainkey." in hostname:
            return ["v=DKIM1; k=rsa; p=SAMPLE_PUBLIC"]
        return ["v=spf1 include:_spf.matemail.pro ~all"]

    def test_inconclusive_dmarc_preserves_prior_health_and_timestamps(self):
        with mock.patch("apps.dnshealth.services._resolve_mx",
                        return_value=["10 mx.matemail.pro"]), mock.patch(
            "apps.dnshealth.services._resolve_txt", side_effect=self.valid_txt,
        ), mock.patch("apps.dnshealth.services._resolve_srv", return_value=[]):
            check_dns_for_domain(self.domain)
        self.assertEqual(self.domain.dns_health_score, 100)
        old = list(DNSRecordCheck.objects.filter(domain=self.domain).values_list(
            "host", "status", "last_checked",
        ))

        def fail_only_dmarc(host):
            if host.startswith("_dmarc."):
                raise DNSLookupInconclusive("Resolvers did not converge")
            return self.valid_txt(host)

        with mock.patch("apps.dnshealth.services._resolve_mx",
                        return_value=[]), mock.patch(
            "apps.dnshealth.services._resolve_txt", side_effect=fail_only_dmarc,
        ), mock.patch("apps.dnshealth.services._resolve_srv", return_value=[]):
            with self.assertRaises(DNSLookupInconclusive):
                check_dns_for_domain(self.domain)

        self.domain.refresh_from_db()
        self.assertEqual(self.domain.dns_health_score, 100)
        current = list(DNSRecordCheck.objects.filter(domain=self.domain).values_list(
            "host", "status", "last_checked",
        ))
        self.assertEqual(old, current)
