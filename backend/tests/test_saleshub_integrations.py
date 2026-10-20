from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pyotp
from django.test import TestCase, override_settings
from django.core.cache import cache
from cryptography.fernet import Fernet
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import TwoFactorSetup, User
from apps.accounts.tokens import make_tokens
from apps.domains.models import Domain
from apps.integrations.models import AccessToken, Integration
from apps.postbox.models import MailboxTwoFactorSetup, MailboxTwoFactorBackupCode
from apps.postbox.two_factor_core import encrypt_totp_secret, hash_backup_code
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
            totp_secret=__import__("apps.accounts.totp_crypto", fromlist=["encrypt_secret"]).encrypt_secret(pyotp.random_base32()),
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
                "two_factor_code": pyotp.TOTP(__import__("apps.accounts.totp_crypto", fromlist=["decrypt_secret"]).decrypt_secret(setup.totp_secret)).now(),
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


    @patch("apps.integrations.views.imap.open_mailbox")
    @patch("apps.integrations.views.sending.submit")
    @patch("apps.integrations.views.sending.assert_organization_may_send")
    @patch("apps.integrations.views.ratelimit.hit")
    @patch("apps.integrations.views.IntegrationSendView.build")
    def test_connected_send_forces_the_approved_mailbox(
        self,
        build,
        rate_hit,
        org_check,
        submit,
        open_mailbox,
    ):
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

        rate_hit.return_value = SimpleNamespace(allowed=True, retry_after=0)
        message = EmailMessage()
        message["Message-ID"] = "<connected-app-test@example.com>"
        build.return_value = (
            message,
            SimpleNamespace(address=self.mailbox.email),
        )
        connection = MagicMock()
        connection.list_folders.return_value = [
            SimpleNamespace(role="sent", name="Sent")
        ]
        open_mailbox.return_value.__enter__.return_value = connection

        response = external.post(
            "/api/integrations/external/send/",
            {
                "to": ["customer@example.net"],
                "subject": "Hello",
                "text": "Test",
                "idempotency_key": "crm-draft-1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        data = build.call_args.args[0]
        self.assertEqual(data["from_address"], self.mailbox.email)
        submit.assert_called_once()
        self.assertEqual(response.data["mailbox"], self.mailbox.email)

    def _new_connection_request(self):
        secret, _ = Integration.issue(
            tenant=self.tenant, mailbox=self.mailbox,
            name="Helpdesk Mailbox MFA Test",
            created_by=self.user, purpose="helpdesk",
            permissions=["mailbox.read", "mail.read", "mail.send"],
        )
        r = APIClient().post("/api/integrations/connect/start/", {
            "tenant_id": str(self.tenant.id), "integration_secret": secret,
        }, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["request_token"]

    @override_settings(
        POSTBOX_2FA_ENABLED=True,
        SMTP_IMAP_SECURITY_ENABLED=True,
        MAIL_ENGINE_ADAPTER="native",
        POSTBOX_2FA_FERNET_KEY=Fernet.generate_key().decode("ascii"),
    )
    def test_new_helpdesk_connection_requires_postbox_mfa_and_keeps_existing_grant(self):
        from django.core.cache import cache
        cache.clear()
        secret = pyotp.random_base32()
        MailboxTwoFactorSetup.objects.create(
            mailbox=self.mailbox, enabled_at=timezone.now(),
            totp_secret_encrypted=encrypt_totp_secret(secret),
        )
        _, existing = Integration.issue(
            tenant=self.tenant, mailbox=self.mailbox,
            name="Existing Helpdesk", created_by=self.user,
            permissions=["mailbox.read", "mail.read", "mail.send"],
        )
        existing_raw, _ = AccessToken.issue(existing)
        external = APIClient()
        external.credentials(
            HTTP_AUTHORIZATION=f"Bearer {existing_raw}",
            HTTP_X_MATEMAIL_TENANT=str(self.tenant.pk),
        )
        self.assertEqual(external.get("/api/integrations/external/profile/").status_code, 200)

        token = self._new_connection_request()
        url = f"/api/integrations/authorize/?request={token}"
        details = self.client.get("/api/integrations/authorize/", {"request": token})
        self.assertEqual(details.status_code, 200)
        self.assertTrue(details.data["mailbox_verification_required"])
        self.assertFalse(details.data["user_2fa_required"])

        without_factor = self.client.post(url, {"password": "Strong-password-123!"}, format="json")
        self.assertEqual(without_factor.status_code, 400)
        wrong_factor = self.client.post(url, {
            "password": "Strong-password-123!", "mailbox_factor_code": "000000",
        }, format="json")
        self.assertEqual(wrong_factor.status_code, 400)
        # Enrollment alone must not invalidate an existing explicitly approved
        # app connection or make it suddenly depend on a fresh OTP per API call.
        self.assertEqual(external.get("/api/integrations/external/profile/").status_code, 200)
        approved = self.client.post(url, {
            "password": "Strong-password-123!",
            "mailbox_factor_code": pyotp.TOTP(secret).now(),
        }, format="json")
        self.assertEqual(approved.status_code, 200, getattr(approved, "data", None))
        self.assertEqual(external.get("/api/integrations/external/profile/").status_code, 200)

    @override_settings(
        POSTBOX_2FA_ENABLED=True,
        SMTP_IMAP_SECURITY_ENABLED=True,
        MAIL_ENGINE_ADAPTER="native",
        POSTBOX_2FA_FERNET_KEY=Fernet.generate_key().decode("ascii"),
    )
    def test_mailbox_backup_code_single_use_for_connected_app_approval(self):
        cache.clear()
        secret = pyotp.random_base32()
        MailboxTwoFactorSetup.objects.create(
            mailbox=self.mailbox, enabled_at=timezone.now(),
            totp_secret_encrypted=encrypt_totp_secret(secret),
        )
        backup = "mailbox-backup-12345678901234567890"
        row = MailboxTwoFactorBackupCode.objects.create(
            mailbox=self.mailbox, code_hash=hash_backup_code(backup),
        )
        first = self._new_connection_request()
        approved = self.client.post(
            f"/api/integrations/authorize/?request={first}",
            {"password": "Strong-password-123!", "mailbox_factor_code": backup},
            format="json",
        )
        self.assertEqual(approved.status_code, 200, getattr(approved, "data", None))
        row.refresh_from_db()
        self.assertIsNotNone(row.used_at)
        second = self._new_connection_request()
        rejected = self.client.post(
            f"/api/integrations/authorize/?request={second}",
            {"password": "Strong-password-123!", "mailbox_factor_code": backup},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("mailbox_factor_code", rejected.data)

    @override_settings(POSTBOX_2FA_ENABLED=True)
    def test_required_but_unenrolled_mailbox_fails_closed_for_new_connections(self):
        token = self._new_connection_request()
        self.tenant.two_factor_policy = "require"
        self.tenant.save(update_fields=["two_factor_policy"])
        with override_settings(
            ORGANIZATION_2FA_POLICY_ENABLED=True,
            ORGANIZATION_2FA_ENFORCEMENT_READY=True,
        ):
            details = self.client.get("/api/integrations/authorize/", {"request": token})
            self.assertTrue(details.data["mailbox_verification_required"])
            refusal = self.client.post(
                f"/api/integrations/authorize/?request={token}",
                {"password": "Strong-password-123!", "mailbox_factor_code": "000000"},
                format="json",
            )
            self.assertEqual(refusal.status_code, 400)
            self.assertIn("mailbox_factor_code", refusal.data)
