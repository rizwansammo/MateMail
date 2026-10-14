"""Phase 6D sensitive mailbox access: explicit grant + recent platform OTP."""
from contextlib import contextmanager
from datetime import timedelta
from email.message import EmailMessage
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.tokens import make_tokens
from apps.platform_admin.mail_center_purpose import recent_platform_otp
from apps.platform_admin.models import PlatformSystemMailboxGrant, PlatformAuditLog
from tests.factories import (
    FAST_PASSWORD_HASHERS, auth_client, disable_throttling, make_tenant, make_user,
)

FLAGS = {
    "PLATFORM_MAIL_CENTER_ENABLED": True,
    "PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED": True,
    "POSTBOX_MASTER_PASSWORD": "test-only-master",
}
ROOT = "/api/platform/mail-center/"
PROOF_ROLE = "security"
ADDRESS = "security@system.matemail.pro"


def platform_client(user, *, proof=True, seconds_old=0):
    from rest_framework_simplejwt.tokens import AccessToken
    tokens = make_tokens(user, platform_verified=proof)
    if proof and seconds_old:
        token = AccessToken(tokens["access"])
        token["platform_otp_verified_at"] -= seconds_old
        tokens["access"] = str(token)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + tokens["access"])
    return client


class FakePurposeIMAP:
    def __init__(self):
        self.operations = []
        msg = EmailMessage()
        msg["From"] = "External Sender <sender@example.test>"
        msg["To"] = ADDRESS
        msg["Subject"] = "Sensitive subject not logged"
        msg.set_content("Sensitive plain text never enters audit.")
        msg.add_alternative(
            '<p>Hello <b>Security</b><script>evil()</script>'
            '<img src="https://remote-pixel.example/track"></p>',
            subtype="html",
        )
        msg.add_attachment(
            b"file payload", maintype="text", subtype="plain",
            filename="confidential.txt",
        )
        self.raw = msg.as_bytes()

    def list_folders(self):
        self.operations.append("list")
        return [
            SimpleNamespace(name="INBOX", role="inbox", selectable=True),
            SimpleNamespace(name="Sent", role="sent", selectable=True),
            SimpleNamespace(name="Hidden", role="", selectable=False),
        ]

    def folder_counts(self, folder):
        self.operations.append(("count", folder))
        return (2, 1)

    def select(self, folder, *, readonly=False):
        self.operations.append(("select", folder, readonly))
        assert readonly, "Purpose mailbox must never select writable"
        return SimpleNamespace(uid_validity=42)

    def search_uids(self, criteria, *, newest=True):
        self.operations.append(("search", criteria, newest))
        assert criteria == ["ALL"] and newest
        return [9, 8]

    def fetch_summaries(self, uids):
        self.operations.append(("summaries", uids))
        if uids == [9]:
            return [SimpleNamespace(
                uid=9, subject="Sensitive subject not logged",
                from_name="External Sender", from_address="sender@example.test",
                date="2026-10-14", seen=False, has_attachments=True,
                size=len(self.raw),
            )]
        if uids == [9, 8]:
            return [
                SimpleNamespace(
                    uid=uid, subject="Sensitive subject not logged",
                    from_name="External Sender", from_address="sender@example.test",
                    date="2026-10-14", seen=False, has_attachments=True,
                    size=len(self.raw),
                ) for uid in uids
            ]
        return []

    def fetch_raw(self, uid):
        self.operations.append(("raw", uid))
        assert uid == 9
        return self.raw

    def append(self, *args, **kwargs):
        raise AssertionError("Read-only sensitive API cannot APPEND")

    def store_flags(self, *args, **kwargs):
        raise AssertionError("Read-only sensitive API cannot STORE")

    def delete_permanently(self, *args, **kwargs):
        raise AssertionError("Read-only sensitive API cannot DELETE")


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, **FLAGS)
class Phase6DGrantTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.superuser = make_user(
            "root-ops@example.test", is_platform_admin=True, is_superuser=True,
        )
        self.admin = make_user("trusted-operator@example.test", is_platform_admin=True)
        self.other_admin = make_user("other-operator@example.test", is_platform_admin=True)
        self.customer_user = make_user("ordinary-customer@example.test")
        tenant = make_tenant(self.customer_user, slug="sensitive-mail-tenant")
        self.client = platform_client(self.superuser)
        self.admin_client = platform_client(self.admin)
        self.tenant_client = auth_client(self.customer_user, tenant)
        self.fake = FakePurposeIMAP()
        self.opened = []

        @contextmanager
        def opened(address):
            self.opened.append(address)
            yield self.fake

        patcher = mock.patch("apps.platform_admin.mail_center_purpose.imap.open_mailbox", side_effect=opened)
        patcher.start()
        self.addCleanup(patcher.stop)

    def grant(self, role="security", user=None, *, hours=24):
        user = user or self.admin
        return PlatformSystemMailboxGrant.objects.create(
            grantee=user, role=role,
            expires_at=timezone.now() + timedelta(hours=hours),
            granted_by=self.superuser,
        )

    def test_unprivileged_sessions_and_api_tokens_rejected(self):
        url = ROOT + "purpose/security/folders/"
        for client in (APIClient(), self.tenant_client, platform_client(self.admin, proof=False)):
            self.assertIn(client.get(url).status_code, (401, 403))
        self.grant()
        self.assertEqual(platform_client(self.admin, seconds_old=3600).get(url).status_code, 403)
        self.assertEqual(platform_client(self.admin, seconds_old=90000).get(url).status_code, 403)
        self.assertEqual(self.opened, [])

    def test_no_implicit_grants_for_platform_administrators_or_superuser(self):
        for client in (self.client, self.admin_client):
            self.assertEqual(client.get(ROOT + "purpose/security/folders/").status_code, 403)
            self.assertEqual(client.get(ROOT + "purpose/noreply/folders/").status_code, 403)
        self.assertEqual(self.admin_client.get(ROOT + "purpose/operations/folders/").status_code, 404)
        self.assertFalse(self.opened)

    def test_gate_disabled_by_default_and_missing_master_password(self):
        self.grant()
        url = ROOT + "purpose/security/folders/"
        with override_settings(PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED=False):
            self.assertEqual(self.admin_client.get(url).status_code, 503)
        with override_settings(PLATFORM_MAIL_CENTER_ENABLED=False):
            self.assertEqual(self.admin_client.get(url).status_code, 503)
        with override_settings(POSTBOX_MASTER_PASSWORD=""):
            self.assertEqual(self.admin_client.get(url).status_code, 503)
        self.assertFalse(self.opened)

    def test_approval_requires_verified_second_superuser(self):
        endpoint = ROOT + "system-access/grants/"
        request = {"grantee_id": str(self.admin.pk), "role": "security", "duration_hours": 24}
        self.assertEqual(self.admin_client.post(endpoint, request, format="json").status_code, 403)
        self.assertEqual(platform_client(self.superuser, proof=False).post(endpoint, request, format="json").status_code, 403)
        self.assertEqual(platform_client(self.superuser, seconds_old=4000).post(endpoint, request, format="json").status_code, 403)
        self.assertEqual(self.client.post(endpoint, {**request, "grantee_id": str(self.superuser.pk)}, format="json").status_code, 403)
        for changed in (
            {"role": "operations"},
            {"role": "tlsrpt"},
            {"duration_hours": 169},
            {"duration_hours": 0},
            {"mailbox": "customer@somewhere.test"},
        ):
            payload = dict(request, **changed)
            self.assertEqual(self.client.post(endpoint, payload, format="json").status_code, 400)
        self.assertEqual(PlatformSystemMailboxGrant.objects.count(), 0)
        self.assertEqual(self.opened, [])

    def test_approval_expiry_no_replay_and_revocation(self):
        endpoint = ROOT + "system-access/grants/"
        payload = {"grantee_id": str(self.admin.pk), "role": "security", "duration_hours": 2}
        result = self.client.post(endpoint, payload, format="json")
        self.assertEqual(result.status_code, 201, result.data)
        grant = PlatformSystemMailboxGrant.objects.get()
        self.assertEqual(grant.role, "security")
        self.assertEqual(grant.granted_by_id, self.superuser.pk)
        self.assertEqual(self.client.post(endpoint, payload, format="json").status_code, 409)
        self.assertEqual(
            self.admin_client.get(ROOT + "system-access/").data["permissions"][0]["access"],
            "read",
        )
        url = ROOT + "purpose/security/folders/"
        self.assertEqual(self.admin_client.get(url).status_code, 200)
        revoke_url = ROOT + "system-access/grants/" + str(grant.pk) + "/"
        self.assertEqual(self.admin_client.delete(revoke_url).status_code, 403)
        self.assertEqual(self.client.delete(revoke_url).status_code, 204)
        self.assertEqual(self.admin_client.get(url).status_code, 403)
        self.assertEqual(PlatformAuditLog.objects.filter(action="mail_center.grant_approved").count(), 1)
        self.assertEqual(PlatformAuditLog.objects.filter(action="mail_center.grant_revoked").count(), 1)
        self.assertNotIn("Sensitive subject", str(PlatformAuditLog.objects.values_list("metadata",flat=True)))

    def test_expired_and_revoked_grants_deny_access(self):
        grant = self.grant(hours=1)
        url = ROOT + "purpose/security/folders/"
        self.assertEqual(self.admin_client.get(url).status_code, 200)
        grant.expires_at = timezone.now() - timedelta(seconds=1)
        grant.save(update_fields=["expires_at"])
        self.assertEqual(self.admin_client.get(url).status_code, 403)
        grant.expires_at = timezone.now() + timedelta(hours=1)
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["expires_at", "revoked_at"])
        self.assertEqual(self.admin_client.get(url).status_code, 403)

    def test_role_isolation_and_no_request_selected_mailbox(self):
        self.grant(role="security")
        security = ROOT + "purpose/security/folders/"
        self.assertEqual(self.admin_client.get(security).status_code, 200)
        self.assertEqual(self.admin_client.get(ROOT + "purpose/noreply/folders/").status_code, 403)
        self.assertEqual(self.opened, [ADDRESS])
        self.assertEqual(self.admin_client.get(security, {"mailbox": "owner@client.test"}).status_code, 400)
        self.assertEqual(self.opened, [ADDRESS])
        self.grant(role="noreply")
        self.assertEqual(self.admin_client.get(ROOT + "purpose/noreply/folders/").status_code, 200)
        self.assertEqual(self.opened[-1], "noreply@system.matemail.pro")
        self.assertEqual(
            self.admin_client.post(ROOT + "purpose/security/folders/", {}, format="json").status_code,
            405,
        )

    def test_header_list_and_sanitized_detail_readonly_and_audited(self):
        self.grant()
        base = ROOT + "purpose/security/"
        headers = self.admin_client.get(base + "messages/", {"folder": "INBOX"})
        self.assertEqual(headers.status_code, 200, headers.data)
        self.assertEqual(headers.data["total"], 2)
        self.assertEqual(headers.data["uid_validity"], 42)
        detail = self.admin_client.get(base + "messages/9/", {"folder": "INBOX", "uid_validity": 42})
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertIn("Security", detail.data["html"])
        self.assertNotIn("remote-pixel.example", detail.data["html"])
        self.assertNotIn("<script", detail.data["html"])
        self.assertEqual(detail.data["text"], "Sensitive plain text never enters audit.")
        self.assertTrue(detail.data["remote_images_blocked"])
        self.assertTrue(all(step[0] != "select" or step[2] is True
                            for step in self.fake.operations if isinstance(step, tuple)))
        audit = PlatformAuditLog.objects.all()
        self.assertFalse(any("Sensitive plain text" in str(item.metadata) for item in audit))
        self.assertFalse(any("sender@example.test" in str(item.metadata) for item in audit))
        self.assertIn("no-store", detail["Cache-Control"])

    def test_attachment_only_download_and_uidvalidity_fail_closed(self):
        self.grant()
        detail_url = ROOT + "purpose/security/messages/9/"
        wrong = self.admin_client.get(detail_url, {"uid_validity": 41})
        self.assertEqual(wrong.status_code, 409)
        self.assertNotIn(("raw", 9), self.fake.operations)
        good = self.admin_client.get(detail_url, {"folder": "INBOX", "uid_validity": 42})
        self.assertEqual(good.status_code, 200)
        part = good.data["attachments"][0]["part_id"]
        attach_url = ROOT + "purpose/security/messages/9/attachments/" + part + "/"
        result = self.admin_client.get(attach_url, {"folder": "INBOX", "uid_validity": 42})
        self.assertEqual(result.status_code, 200, result.data if result.status_code != 200 else "")
        self.assertEqual(result["Content-Type"], "application/octet-stream")
        self.assertIn("attachment;", result["Content-Disposition"])
        self.assertIn("sandbox", result["Content-Security-Policy"])
        self.assertIn("no-store", result["Cache-Control"])
        self.assertEqual(result.content, b"file payload")
        self.assertEqual(self.admin_client.get(attach_url, {"folder": "INBOX", "uid_validity": 41}).status_code, 409)

    def test_grant_fails_closed_if_audit_unavailable(self):
        endpoint = ROOT + "system-access/grants/"
        payload = {"grantee_id": str(self.admin.pk), "role": "security", "duration_hours": 2}
        with mock.patch(
            "apps.platform_admin.mail_center_purpose.record_platform_action",
            side_effect=RuntimeError("audit down"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(endpoint, payload, format="json")
        self.assertFalse(PlatformSystemMailboxGrant.objects.exists())

    def test_recent_otp_claim_is_not_given_to_normal_sessions(self):
        self.assertFalse(recent_platform_otp(SimpleNamespace(auth=None)))
        from rest_framework_simplejwt.tokens import AccessToken
        normal = make_tokens(self.admin)
        token = AccessToken(normal["access"])
        self.assertNotIn("platform_otp_verified_at", token)
        verified = AccessToken(make_tokens(self.admin, platform_verified=True)["access"])
        self.assertIn("platform_otp_verified_at", verified)
        self.assertTrue(recent_platform_otp(SimpleNamespace(auth=verified)))
