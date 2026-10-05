from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.tenants.models import (
    CustomHostname,
    CustomHostnameCertificateStatus,
    CustomHostnameDNSStatus,
    CustomHostnameProvisioningStatus,
)
from tests.factories import FAST_PASSWORD_HASHERS, make_tenant, make_user


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CUSTOM_HOST_CNAME_TARGET="custom.matemail.online",
    CUSTOM_HOST_RESERVED_SUFFIXES=("matemail.online",),
    DEDICATED_TENANT_HOSTS={
        "mailadmin.netamate.com": "netamate-solutions",
        "postbox.netamate.com": "netamate-solutions",
    },
)
class DedicatedCustomHostnameAdoptionTest(TestCase):
    def setUp(self):
        owner = make_user("owner@netamate.example")
        self.tenant = make_tenant(
            owner,
            name="NetaMate Solutions",
            slug="netamate-solutions",
        )

    @mock.patch(
        "apps.tenants.management.commands.adopt_dedicated_custom_hostname."
        "check_custom_hostname_dns",
        return_value=(True, "verified", ""),
    )
    def test_operator_can_stage_existing_dedicated_host_for_worker_activation(
        self, _dns
    ):
        output = StringIO()
        call_command(
            "adopt_dedicated_custom_hostname",
            tenant_slug="netamate-solutions",
            hostname="MAILADMIN.NETAMATE.COM.",
            surface="hub",
            stdout=output,
        )

        row = CustomHostname.objects.get(hostname="mailadmin.netamate.com")
        self.assertEqual(row.tenant, self.tenant)
        self.assertEqual(row.surface, "hub")
        self.assertEqual(row.dns_status, CustomHostnameDNSStatus.VERIFIED)
        self.assertEqual(
            row.provisioning_status,
            CustomHostnameProvisioningStatus.READY,
        )
        self.assertEqual(
            row.certificate_status,
            CustomHostnameCertificateStatus.ACTIVE,
        )
        self.assertIn("root-owned worker", output.getvalue())

    @mock.patch(
        "apps.tenants.management.commands.adopt_dedicated_custom_hostname."
        "check_custom_hostname_dns",
        return_value=(False, "CNAME not found", "no CNAME answer"),
    )
    def test_adoption_requires_the_normal_direct_cname_contract(self, _dns):
        with self.assertRaises(CommandError):
            call_command(
                "adopt_dedicated_custom_hostname",
                tenant_slug="netamate-solutions",
                hostname="postbox.netamate.com",
                surface="postbox",
            )
        self.assertFalse(CustomHostname.objects.exists())

    @mock.patch(
        "apps.tenants.management.commands.adopt_dedicated_custom_hostname."
        "check_custom_hostname_dns",
        return_value=(True, "verified", ""),
    )
    def test_adoption_refuses_a_host_not_bound_by_deployment_config(self, _dns):
        with self.assertRaises(CommandError):
            call_command(
                "adopt_dedicated_custom_hostname",
                tenant_slug="netamate-solutions",
                hostname="other.netamate.com",
                surface="hub",
            )
        self.assertFalse(CustomHostname.objects.exists())

    @mock.patch(
        "apps.tenants.management.commands.adopt_dedicated_custom_hostname."
        "check_custom_hostname_dns",
        return_value=(True, "verified", ""),
    )
    def test_already_active_adoption_is_idempotent(self, _dns):
        CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="mailadmin.netamate.com",
            surface="hub",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )

        call_command(
            "adopt_dedicated_custom_hostname",
            tenant_slug="netamate-solutions",
            hostname="mailadmin.netamate.com",
            surface="hub",
            stdout=StringIO(),
        )

        self.assertEqual(
            CustomHostname.objects.filter(
                hostname="mailadmin.netamate.com"
            ).count(),
            1,
        )
