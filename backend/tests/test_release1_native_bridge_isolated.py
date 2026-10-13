"""Disposable release 1 Django to Native HTTP QA; never run in production."""
import os
import unittest
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from tests.factories import make_user, make_tenant, auth_client, disable_throttling
from apps.platform_admin.system_mail import DEFAULT_PROPOSAL
from apps.platform_admin.models import PlatformAuditLog

@unittest.skipUnless(os.getenv("MATEMAIL_ISOLATED_E2E") == "yes", "isolated-only")
class PlatformDjangoNativeBridgeTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("qa-phase3-admin@isolated.test", is_platform_admin=True)
        self.customer = make_user("qa-phase3-tenant@isolated.test")
        self.tenant = make_tenant(self.customer, name="QA Tenant", slug="isolated-phase3-qa")
        self.admin_client = auth_client(self.admin)
        self.customer_client = auth_client(self.customer, self.tenant)

    def test_platform_staging_and_readiness_over_real_native_http(self):
        with override_settings(
            MAIL_ENGINE_ADAPTER="native",
            SYSTEM_MAIL_STAGING_ENABLED=True,
            NATIVE_ENGINE_API_URL="http://matemail-r1-native-api:8451",
            NATIVE_ENGINE_PLATFORM_SECRET=os.environ["NATIVE_PLATFORM_SECRET"],
            NATIVE_ENGINE_API_SECRET=os.environ["NATIVE_API_SECRET"],
        ):
            new = self.admin_client.post(
                "/api/platform/system-mail/configuration/",
                dict(DEFAULT_PROPOSAL, expected_revision=0), format="json",
            )
            self.assertEqual(new.status_code,201,new.data)
            intent={"expected_revision":1, "confirmation":"stage_inactive"}
            self.assertIn(self.customer_client.post(
                "/api/platform/system-mail/stage/",intent,format="json"
            ).status_code,(401,403))
            self.assertIn(APIClient().post(
                "/api/platform/system-mail/stage/",intent,format="json"
            ).status_code,(401,403))
            result=self.admin_client.post(
                "/api/platform/system-mail/stage/",intent,format="json"
            )
            self.assertEqual(result.status_code,200,result.data)
            self.assertFalse(result.data["delivery_activated"])
            self.assertFalse(result.data["activation_supported"])
            self.assertEqual(PlatformAuditLog.objects.filter(action="system_mail.stage_requested").count(),1)
            self.assertEqual(PlatformAuditLog.objects.filter(action="system_mail.stage_confirmed").count(),1)
            readiness=self.admin_client.get("/api/platform/system-mail/readiness/")
            self.assertEqual(readiness.status_code,200,readiness.data)
            self.assertEqual(readiness.data["engine"]["status"],"observed",readiness.data)
            self.assertEqual(readiness.data["engine"]["target_domain"],"inactive")
            self.assertEqual(readiness.data["engine"]["target_roles"]["noreply_address"],"inactive")
            self.assertFalse(readiness.data["cutover_ready"])
            self.assertFalse(readiness.data["activation_supported"])
            print("ISOLATED_DJANGO_TO_NATIVE_REAL_HTTP_PASS",flush=True)
