"""Phase 6F: capability flags, sensitive grant inventory and production safety."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tokens import make_tokens
from apps.platform_admin.models import PlatformAuditLog, PlatformSystemMailboxGrant
from tests.factories import (
    FAST_PASSWORD_HASHERS, auth_client, disable_throttling, make_tenant, make_user,
)

CAPS = "/api/platform/mail-center/capabilities/"
INVENTORY = "/api/platform/mail-center/system-access/manage/"


def verified_client(user):
    tokens = make_tokens(user, platform_verified=True)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])
    return client


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class MailCenterRolloutSafetyTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.root = make_user(
            "phase6f-platform-root@example.test",
            is_platform_admin=True, is_superuser=True,
        )
        self.admin = make_user("phase6f-platform-admin@example.test", is_platform_admin=True)
        self.customer = make_user("phase6f-customer@example.test")
        self.tenant = make_tenant(self.customer, slug="phase6f-customer-tenant")
        self.root_client = verified_client(self.root)
        self.admin_client = verified_client(self.admin)
        self.customer_client = auth_client(self.customer, self.tenant)

    def test_default_capabilities_fail_closed_without_secrets(self):
        for client in (APIClient(), self.customer_client):
            self.assertIn(client.get(CAPS).status_code, (401, 403))
        with override_settings(
            PLATFORM_MAIL_CENTER_ENABLED=False,
            PLATFORM_MAIL_CENTER_SEND_ENABLED=False,
            PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED=False,
            PLATFORM_MAIL_CENTER_REPORTS_ENABLED=False,
            POSTBOX_MASTER_PASSWORD="",
        ):
            response = self.admin_client.get(CAPS)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {
            "operations": False, "sending": False,
            "sensitive_access": False, "reporting": False,
        })
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.admin_client.post(CAPS, {}, format="json").status_code, 405)
        self.assertEqual(self.admin_client.get(CAPS + "?mailbox=security@system.matemail.pro").status_code, 400)
        self.assertFalse(PlatformAuditLog.objects.exists())

    @override_settings(
        PLATFORM_MAIL_CENTER_ENABLED=True,
        PLATFORM_MAIL_CENTER_SEND_ENABLED=True,
        PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED=True,
        PLATFORM_MAIL_CENTER_REPORTS_ENABLED=True,
        EMAIL_HOST="smtp.test.example",
        POSTBOX_MASTER_PASSWORD="test-only-not-a-real-secret",
    )
    def test_capabilities_show_only_coarse_boolean_state(self):
        r = self.admin_client.get(CAPS)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.data), {
            "operations", "sending", "sensitive_access", "reporting",
        })
        self.assertTrue(all(r.data.values()))
        self.assertNotIn("smtp.test.example", str(r.data))
        self.assertNotIn("test-only-not-a-real-secret", str(r.data))
        with override_settings(POSTBOX_MASTER_PASSWORD=""):
            unavailable = self.admin_client.get(CAPS).data
        self.assertFalse(unavailable["operations"])
        self.assertFalse(unavailable["sending"])
        self.assertFalse(unavailable["sensitive_access"])
        self.assertTrue(unavailable["reporting"])

    @override_settings(
        PLATFORM_MAIL_CENTER_ENABLED=True,
        PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED=True,
    )
    def test_only_verified_superuser_can_list_manageable_grants(self):
        grant = PlatformSystemMailboxGrant.objects.create(
            grantee=self.admin, granted_by=self.root,
            role="security", expires_at=timezone.now() + timedelta(hours=2),
        )
        for client in (APIClient(), self.customer_client, self.admin_client, auth_client(self.root)):
            self.assertIn(client.get(INVENTORY).status_code, (401, 403))
        result = self.root_client.get(INVENTORY)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(len(result.data["results"]), 1)
        self.assertEqual(result.data["results"][0]["grant_id"], str(grant.pk))
        self.assertEqual(result.data["results"][0]["grantee_email"], self.admin.email)
        self.assertIn("no-store", result["Cache-Control"])
        self.assertFalse(result.data["truncated"])
        self.assertEqual(
            PlatformAuditLog.objects.get(action="mail_center.grants_inventory_read").metadata,
            {"count": 1},
        )
        self.assertNotIn(self.admin.email, str(PlatformAuditLog.objects.first().metadata))

    @override_settings(
        PLATFORM_MAIL_CENTER_ENABLED=True,
        PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED=True,
    )
    def test_inventory_no_expired_recalled_or_cross_tenant_mail(self):
        PlatformSystemMailboxGrant.objects.create(
            grantee=self.admin, granted_by=self.root, role="security",
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        result = self.root_client.get(INVENTORY)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["results"], [])
        self.assertNotIn(self.customer.email, str(result.data))
        self.assertEqual(self.root_client.post(INVENTORY, {}, format="json").status_code, 405)
        self.assertEqual(self.root_client.get(INVENTORY + "?role=security").status_code, 400)

    @override_settings(
        PLATFORM_MAIL_CENTER_ENABLED=True,
        PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED=False,
    )
    def test_inventory_separate_gate(self):
        self.assertEqual(self.root_client.get(INVENTORY).status_code, 503)
        self.assertFalse(PlatformAuditLog.objects.exists())
