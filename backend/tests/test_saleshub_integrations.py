import pyotp
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import TwoFactorSetup, User
from apps.accounts.tokens import make_tokens
from apps.domains.models import Domain
from apps.integrations.models import AccessToken, Integration
from apps.mailboxes.models import Mailbox
from apps.tenants.models import (
    MemberRole,
    MemberStatus,
    Tenant,
    TenantMembership,
    TenantStatus,
)


class ConnectedAppIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="Strong-password-123!",
            email_verified=True,
        )
        self.tenant = Tenant.objects.create(
            name="Example",
            slug="example",
            owner=self.user,
            status=TenantStatus.ACTIVE,
            approved_at=timezone.now(),
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=MemberRole.OWNER,
            status=MemberStatus.ACTIVE,
        )
        self.domain = Domain.objects.create(
            tenant=self.tenant,
            domain="example.com",
        )
        self.mailbox = Mailbox.objects.create(
            tenant=self.tenant,
            domain=self.domain,
            local_part="sales",
            full_name="Sales",
            status="active",
            mail_engine_provisioned=True,
        )
        self.client = APIClient()
        token = make_tokens(self.user, tenant_id=self.tenant.id)["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_secret_is_shown_once_and_bound_to_one_mailbox(self):
        response = self.client.post(
            "/api/integrations/",
            {
                "name": "Example CRM",
                "purpose": "sales_crm",
                "mailbox_id": str(self.mailbox.id),
                "permissions": ["mailbox.read", "mail.send", "signatures.read"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        secret = response.data["integration_secret"]
        self.assertTrue(secret.startswith("mmi_"))

        integration = Integration.objects.get(pk=response.data["id"])
        self.assertEqual(integration.mailbox_id, self.mailbox.id)
        self.assertNotEqual(integration.secret_hash, secret)

        listed = self.client.get("/api/integrations/")
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("integration_secret", listed.data[0])

    def test_connect_requires_fresh_2fa_when_enabled(self):
        secret, _ = Integration.issue(
            tenant=self.tenant,
            mailbox=self.mailbox,
            name="Example App",
            created_by=self.user,
            permissions=["mailbox.read", "mail.send"],
        )
        public = APIClient()
        started = public.post(
            "/api/integrations/connect/start/",
            {
                "tenant_id": str(self.tenant.id),
                "integration_secret": secret,
            },
            format="json",
        )
        self.assertEqual(started.status_code, 200)

        setup = TwoFactorSetup.objects.create(
            user=self.user,
            totp_secret=pyotp.random_base32(),
        )
        self.user.two_factor_enabled = True
        self.user.save(update_fields=["two_factor_enabled"])

        request_token = started.data["request_token"]
        details = self.client.get(
            "/api/integrations/authorize/",
            {"request": request_token},
        )
        self.assertEqual(details.status_code, 200)
        self.assertTrue(details.data["user_2fa_required"])

        denied = self.client.post(
            f"/api/integrations/authorize/?request={request_token}",
            {"password": "Strong-password-123!"},
            format="json",
        )
        self.assertEqual(denied.status_code, 400)

        approved = self.client.post(
            f"/api/integrations/authorize/?request={request_token}",
            {
                "password": "Strong-password-123!",
                "two_factor_code": pyotp.TOTP(setup.totp_secret).now(),
            },
            format="json",
        )
        self.assertEqual(approved.status_code, 200)

        status = public.post(
            "/api/integrations/connect/status/",
            {
                "tenant_id": str(self.tenant.id),
                "poll_token": started.data["poll_token"],
            },
            format="json",
        )
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.data["access_token"].startswith("mmc_"))

    def test_access_token_is_revocable_and_tenant_bound(self):
        _, integration = Integration.issue(
            tenant=self.tenant,
            mailbox=self.mailbox,
            name="Example App",
            created_by=self.user,
            permissions=["mailbox.read", "mail.send"],
        )
        raw, token = AccessToken.issue(integration)
        external = APIClient()
        external.credentials(
            HTTP_AUTHORIZATION=f"Bearer {raw}",
            HTTP_X_MATEMAIL_TENANT=str(self.tenant.id),
        )

        profile = external.get("/api/integrations/external/profile/")
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.data["mailbox_email"], "sales@example.com")

        wrong_tenant = APIClient()
        wrong_tenant.credentials(
            HTTP_AUTHORIZATION=f"Bearer {raw}",
            HTTP_X_MATEMAIL_TENANT="00000000-0000-0000-0000-000000000001",
        )
        self.assertEqual(
            wrong_tenant.get("/api/integrations/external/profile/").status_code,
            401,
        )

        disconnected = external.post(
            "/api/integrations/external/disconnect/",
            {},
            format="json",
        )
        self.assertEqual(disconnected.status_code, 200)
        self.assertIsNotNone(
            AccessToken.objects.get(pk=token.pk).revoked_at
        )
        self.assertEqual(
            external.get("/api/integrations/external/profile/").status_code,
            401,
        )


    def test_scope_denies_unapproved_mail_read(self):
        _, integration = Integration.issue(
            tenant=self.tenant,
            mailbox=self.mailbox,
            name="CRM",
            purpose="sales_crm",
            created_by=self.user,
            permissions=["mailbox.read", "mail.send"],
        )
        raw, _ = AccessToken.issue(integration)
        external = APIClient()
        external.credentials(
            HTTP_AUTHORIZATION=f"Bearer {raw}",
            HTTP_X_MATEMAIL_TENANT=str(self.tenant.id),
        )
        response = external.get("/api/integrations/external/messages/")
        self.assertEqual(response.status_code, 403)

    def test_helpdesk_preset_grants_mail_read_and_modify(self):
        response = self.client.post(
            "/api/integrations/",
            {
                "name": "External Helpdesk",
                "purpose": "helpdesk",
                "mailbox_id": str(self.mailbox.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        keys = {item["key"] for item in response.data["permissions"]}
        self.assertEqual(
            keys,
            {"mailbox.read", "mail.read", "mail.modify", "mail.send"},
        )
