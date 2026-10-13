"""Phase 5 system domain DNS evidence is observational and fail closed."""
from unittest import mock

import dns.resolver
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from apps.platform_admin.system_mail_dns import inspect_system_mail_dns
from tests.factories import auth_client, make_user, make_tenant, disable_throttling


class TextRecord:
    def __init__(self, value):
        self.strings = [value.encode()]


class MXRecord:
    preference = 10
    exchange = "mx.matemail.pro."


class Resolver:
    def __init__(self, records):
        self.records = records

    def resolve(self, name, kind, **kwargs):
        value = self.records.get((name, kind), [])
        if value == "timeout":
            raise dns.resolver.Timeout()
        return value


def valid_records():
    domain = "system.matemail.pro"
    return {
        (domain, "MX"): [MXRecord()],
        (domain, "TXT"): [TextRecord("v=spf1 include:_spf.matemail.pro -all")],
        (f"_dmarc.{domain}", "TXT"): [TextRecord("v=DMARC1; p=none; rua=mailto:dmarc@mail.matemail.pro")],
        (f"mm1._domainkey.{domain}", "TXT"): [TextRecord("v=DKIM1; k=rsa; p=FAKE_PUBLIC_KEY_FOR_TEST")],
    }


class DNSObservationTests(SimpleTestCase):
    def test_all_missing_fails_closed(self):
        data = inspect_system_mail_dns(resolver=Resolver({}))
        self.assertEqual(data["mx"], "missing_or_mismatched")
        self.assertEqual(data["dkim_publication"], "missing_or_mismatched")
        self.assertFalse(data["cutover_ready"])

    def test_existing_dns_does_not_claim_cutover_or_dkim_key_match(self):
        data = inspect_system_mail_dns(resolver=Resolver(valid_records()))
        self.assertEqual(data["mx"], "observed")
        self.assertEqual(data["spf"], "observed")
        self.assertEqual(data["dmarc"], "observed")
        self.assertEqual(data["dkim_publication"], "published_key_unmatched")
        self.assertFalse(data["dkim_engine_key_verified"])
        self.assertFalse(data["cutover_ready"])

    def test_duplicate_spf_fails_closed(self):
        records = valid_records()
        records[("system.matemail.pro", "TXT")].append(
            TextRecord("v=spf1 -all")
        )
        self.assertEqual(
            inspect_system_mail_dns(resolver=Resolver(records))["spf"],
            "missing_or_mismatched",
        )

    def test_resolver_timeout_fails_closed(self):
        records = valid_records()
        records[("system.matemail.pro", "MX")] = "timeout"
        self.assertEqual(
            inspect_system_mail_dns(resolver=Resolver(records))["mx"],
            "missing_or_mismatched",
        )


class DNSReadinessPermissionTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.platform = make_user("phase5-platform@test.example", is_platform_admin=True)
        self.customer = make_user("phase5-customer@test.example")
        self.tenant = make_tenant(self.customer, name="Phase5", slug="phase5")
        self.admin_client = auth_client(self.platform)
        self.tenant_client = auth_client(self.customer, self.tenant)
        self.url = "/api/platform/system-mail/dns-readiness/"

    def test_anonymous_and_tenant_cannot_read(self):
        with mock.patch(
            "apps.platform_admin.system_mail_dns.inspect_system_mail_dns"
        ) as inspect:
            self.assertIn(APIClient().get(self.url).status_code, (401, 403))
            self.assertIn(self.tenant_client.get(self.url).status_code, (401, 403))
            inspect.assert_not_called()

    def test_platform_only_read_no_activation(self):
        with mock.patch(
            "apps.platform_admin.system_mail_dns.inspect_system_mail_dns",
            return_value={"cutover_ready": False},
        ) as inspect:
            response = self.admin_client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["cutover_ready"])
        inspect.assert_called_once_with()
