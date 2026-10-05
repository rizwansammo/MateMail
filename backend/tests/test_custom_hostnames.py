from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.cookies import REFRESH_COOKIE_NAME
from apps.accounts.tokens import make_tokens
from apps.mailboxes.models import MailboxStatus
from apps.postbox import auth as postbox_auth
from apps.tenants.models import (
    CustomHostname,
    CustomHostnameCertificateStatus,
    CustomHostnameDNSStatus,
    CustomHostnameProvisioningStatus,
    MemberRole,
    TenantStatus,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    add_member,
    auth_client,
    make_domain,
    make_mailbox,
    make_tenant,
    make_unapproved_tenant,
    make_user,
)


BASE_SETTINGS = dict(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    CUSTOM_HOST_CNAME_TARGET="custom.matemail.online",
    CUSTOM_HOST_RESERVED_SUFFIXES=("matemail.online",),
)


@override_settings(**BASE_SETTINGS)
class CustomHostnameTenantAPITest(TestCase):
    def setUp(self):
        self.owner = make_user("owner@acme.example")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        self.client = auth_client(self.owner, self.tenant)

    def create(self, hostname="mail.customer.com", surface="postbox"):
        return self.client.post(
            "/api/custom-hostnames/",
            {"hostname": hostname, "surface": surface},
            format="json",
        )

    def test_admin_can_add_a_normalized_hostname_and_get_one_cname_instruction(self):
        response = self.create("  Mail.Customer.COM.  ")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["hostname"], "mail.customer.com")
        self.assertEqual(response.data["surface"], "postbox")
        self.assertEqual(response.data["cname_record_type"], "CNAME")
        self.assertEqual(response.data["cname_target"], "custom.matemail.online")
        self.assertEqual(response.data["dns_status"], "pending")
        self.assertEqual(response.data["provisioning_status"], "unprovisioned")

    def test_url_port_wildcard_and_matemail_owned_names_are_rejected(self):
        for hostname in (
            "https://mail.customer.com",
            "mail.customer.com:443",
            "*.customer.com",
            "postbox.matemail.online",
        ):
            with self.subTest(hostname=hostname):
                response = self.create(hostname)
                self.assertEqual(response.status_code, 400)

    def test_only_one_live_hostname_per_surface_per_tenant(self):
        self.assertEqual(self.create("mail.customer.com").status_code, 201)
        response = self.create("inbox.customer.com")

        self.assertEqual(response.status_code, 409)
        self.assertIn("already has", response.data["detail"])

    def test_live_hostname_is_globally_exclusive(self):
        self.assertEqual(self.create("mail.customer.com").status_code, 201)

        other_owner = make_user("owner@other.example")
        other = make_tenant(other_owner, name="Other", slug="other")
        other_client = auth_client(other_owner, other)

        response = other_client.post(
            "/api/custom-hostnames/",
            {"hostname": "mail.customer.com", "surface": "hub"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_inactive_history_releases_hostname_and_surface_slot(self):
        created = self.create("mail.customer.com")
        self.assertEqual(created.status_code, 201)

        removed = self.client.delete(f"/api/custom-hostnames/{created.data['id']}/")
        self.assertEqual(removed.status_code, 204)

        replacement = self.create("inbox.customer.com")
        self.assertEqual(replacement.status_code, 201)

        other_owner = make_user("owner@other.example")
        other = make_tenant(other_owner, name="Other", slug="other")
        other_client = auth_client(other_owner, other)
        reused = other_client.post(
            "/api/custom-hostnames/",
            {"hostname": "mail.customer.com", "surface": "hub"},
            format="json",
        )
        self.assertEqual(reused.status_code, 201)

    def test_read_only_member_cannot_change_custom_hostnames(self):
        reader = make_user("reader@acme.example")
        add_member(self.tenant, reader, MemberRole.READ_ONLY)
        client = auth_client(reader, self.tenant)

        response = client.post(
            "/api/custom-hostnames/",
            {"hostname": "mail.customer.com", "surface": "postbox"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_cross_tenant_detail_is_not_visible(self):
        created = self.create("mail.customer.com")
        other_owner = make_user("owner@other.example")
        other = make_tenant(other_owner, name="Other", slug="other")
        other_client = auth_client(other_owner, other)

        response = other_client.get(f"/api/custom-hostnames/{created.data['id']}/")
        self.assertEqual(response.status_code, 404)

    def test_unapproved_workspace_cannot_add_hostname(self):
        owner = make_user("pending@example.com")
        tenant = make_unapproved_tenant(owner, name="Pending", slug="pending")
        client = auth_client(owner, tenant)

        response = client.post(
            "/api/custom-hostnames/",
            {"hostname": "mail.pending.com", "surface": "postbox"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    @mock.patch(
        "apps.tenants.custom_hosts._lookup_cname_targets",
        return_value=(["custom.matemail.online"], ""),
    )
    def test_verify_accepts_only_the_configured_cname(self, lookup):
        created = self.create("mail.customer.com")
        response = self.client.post(
            f"/api/custom-hostnames/{created.data['id']}/verify/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["verified"])
        self.assertEqual(response.data["custom_hostname"]["dns_status"], "verified")
        lookup.assert_called_once_with("mail.customer.com")

    @mock.patch(
        "apps.tenants.custom_hosts._lookup_cname_targets",
        return_value=(["somewhere-else.example"], ""),
    )
    def test_verify_failure_stays_unprovisioned(self, _lookup):
        created = self.create("mail.customer.com")
        response = self.client.post(
            f"/api/custom-hostnames/{created.data['id']}/verify/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.data["verified"])
        self.assertEqual(response.data["custom_hostname"]["dns_status"], "failed")
        self.assertEqual(
            response.data["custom_hostname"]["provisioning_status"],
            "unprovisioned",
        )


    @mock.patch(
        "apps.tenants.custom_hosts._lookup_cname_targets",
        return_value=(["custom.matemail.online"], ""),
    )
    def test_fresh_verify_explicitly_requeues_a_provisioning_error(self, _lookup):
        row = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="retry.customer.com",
            surface="postbox",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ERROR,
            certificate_status=CustomHostnameCertificateStatus.ERROR,
            last_error="old edge failure",
        )

        response = self.client.post(
            f"/api/custom-hostnames/{row.id}/verify/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(
            row.provisioning_status,
            CustomHostnameProvisioningStatus.UNPROVISIONED,
        )
        self.assertEqual(
            row.certificate_status,
            CustomHostnameCertificateStatus.NOT_REQUESTED,
        )
        self.assertEqual(row.last_error, "")

    def test_suspended_workspace_can_still_relinquish_an_unprovisioned_hostname(self):
        created = self.create("mail.customer.com")
        self.assertEqual(created.status_code, 201)
        self.tenant.status = TenantStatus.SUSPENDED
        self.tenant.save(update_fields=["status", "updated_at"])

        response = self.client.delete(f"/api/custom-hostnames/{created.data['id']}/")
        self.assertEqual(response.status_code, 204)

    def test_customer_cannot_remove_hostname_after_edge_work_has_started(self):
        row = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="mail.customer.com",
            surface="postbox",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.PROVISIONING,
            certificate_status=CustomHostnameCertificateStatus.ISSUING,
        )

        response = self.client.delete(f"/api/custom-hostnames/{row.id}/")
        self.assertEqual(response.status_code, 409)
        row.refresh_from_db()
        self.assertEqual(row.provisioning_status, "provisioning")


@override_settings(
    **BASE_SETTINGS,
    CUSTOM_HOST_PROVISIONER_SECRET="phase2-test-provisioner-secret",
)
class CustomHostnameInternalAPITest(TestCase):
    def setUp(self):
        self.owner = make_user("owner@acme.example")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        self.row = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="mail.customer.com",
            surface="postbox",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
        )
        self.client = APIClient()

    def auth(self):
        self.client.credentials(
            HTTP_X_MATEMAIL_CUSTOM_HOST_SECRET="phase2-test-provisioner-secret"
        )

    def test_internal_endpoints_fail_closed_without_their_own_secret(self):
        response = self.client.get("/api/internal/custom-hostnames/pending/")
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            "/api/internal/custom-hostnames/authorize/",
            {"hostname": "mail.customer.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_pending_queue_contains_verified_unprovisioned_and_crash_recovery_rows(self):
        CustomHostname.objects.create(
            tenant=make_tenant(
                make_user("other@example.com"),
                name="Other",
                slug="other",
            ),
            hostname="hub.other.com",
            surface="hub",
            dns_status=CustomHostnameDNSStatus.FAILED,
        )
        recovering = CustomHostname.objects.create(
            tenant=make_tenant(
                make_user("recover@example.com"),
                name="Recover",
                slug="recover",
            ),
            hostname="hub.recover.com",
            surface="hub",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.PROVISIONING,
            certificate_status=CustomHostnameCertificateStatus.ISSUING,
        )
        self.auth()

        response = self.client.get("/api/internal/custom-hostnames/pending/")

        self.assertEqual(response.status_code, 200)
        hostnames = {item["hostname"] for item in response.data["results"]}
        self.assertEqual(hostnames, {"mail.customer.com", recovering.hostname})
        first = next(
            item for item in response.data["results"]
            if item["hostname"] == "mail.customer.com"
        )
        self.assertEqual(first["hostname"], "mail.customer.com")
        self.assertEqual(
            first["cname_target"],
            "custom.matemail.online",
        )

    def test_authorize_requires_a_verified_live_database_mapping(self):
        self.auth()

        response = self.client.post(
            "/api/internal/custom-hostnames/authorize/",
            {"hostname": "MAIL.CUSTOMER.COM."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["approved"])
        self.assertEqual(response.data["tenant_id"], str(self.tenant.id))

        self.row.dns_status = CustomHostnameDNSStatus.FAILED
        self.row.save(update_fields=["dns_status", "updated_at"])

        response = self.client.post(
            "/api/internal/custom-hostnames/authorize/",
            {"hostname": "mail.customer.com"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.data["approved"])

    def test_worker_can_advance_to_ready_then_activate(self):
        self.auth()

        response = self.client.post(
            f"/api/internal/custom-hostnames/{self.row.id}/state/",
            {
                "provisioning_status": "provisioning",
                "certificate_status": "issuing",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["provisioning_status"], "provisioning")

        response = self.client.post(
            f"/api/internal/custom-hostnames/{self.row.id}/state/",
            {
                "provisioning_status": "ready",
                "certificate_status": "active",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["provisioning_status"], "ready")
        self.assertEqual(response.data["certificate_status"], "active")

        pending = self.client.get(
            "/api/internal/custom-hostnames/activation-pending/"
        )
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(
            [item["hostname"] for item in pending.data["results"]],
            ["mail.customer.com"],
        )

        response = self.client.post(
            f"/api/internal/custom-hostnames/{self.row.id}/state/",
            {
                "provisioning_status": "active",
                "certificate_status": "active",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["provisioning_status"], "active")
        self.assertIsNotNone(response.data["activated_at"])

        pending = self.client.get(
            "/api/internal/custom-hostnames/activation-pending/"
        )
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.data["results"], [])


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    CUSTOM_HOSTS_DYNAMIC_ENABLED=True,
    ALLOWED_HOSTS=["*"],
    CUSTOM_HOST_FIXED_HOSTS=("testserver",),
    CUSTOM_HOST_CACHE_TTL=30,
)
class CustomHostnameHostGuardTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("owner@acme.example")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_unknown_host_is_rejected_before_the_application(self):
        response = APIClient().get(
            "/api/workspaces/",
            HTTP_HOST="unknown.customer.com",
        )
        self.assertEqual(response.status_code, 400)

    def test_fixed_host_still_reaches_the_application(self):
        response = APIClient().get("/api/workspaces/", HTTP_HOST="testserver")
        self.assertEqual(response.status_code, 401)

    def test_only_active_custom_hostname_is_accepted(self):
        active = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="mail.customer.com",
            surface="postbox",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )

        response = APIClient().get(
            "/api/workspaces/",
            HTTP_HOST=active.hostname,
        )
        # The hostname is accepted, then the PostBox surface guard hides
        # Workspace APIs on it.
        self.assertEqual(response.status_code, 404)

        other_tenant = make_tenant(
            make_user("other@example.com"),
            name="Other",
            slug="other",
        )
        ready = CustomHostname.objects.create(
            tenant=other_tenant,
            hostname="hub.other.com",
            surface="hub",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.READY,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )
        cache.clear()

        response = APIClient().get(
            "/api/workspaces/",
            HTTP_HOST=ready.hostname,
        )
        self.assertEqual(response.status_code, 400)


    def test_active_custom_hub_login_is_bound_to_its_tenant(self):
        other = make_tenant(self.owner, name="Other", slug="other-tenant")
        hub = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="hub.customer.com",
            surface="hub",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )
        cache.clear()

        response = APIClient().post(
            "/api/auth/login/",
            {"email": self.owner.email, "password": TEST_PASSWORD},
            format="json",
            HTTP_HOST=hub.hostname,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["tenant"]["slug"], self.tenant.slug)
        self.assertNotEqual(response.data["tenant"]["slug"], other.slug)

    def test_custom_hub_blocks_postbox_and_platform_apis(self):
        hub = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="hub.customer.com",
            surface="hub",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )
        cache.clear()

        for path in (
            "/api/postbox/me/",
            "/api/platform/stats/",
            "/api/internal/health/",
        ):
            with self.subTest(path=path):
                response = APIClient().get(path, HTTP_HOST=hub.hostname)
                self.assertEqual(response.status_code, 404)

    def test_custom_postbox_exposes_only_postbox_api_and_binds_mailbox_tenant(self):
        host = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="inbox.customer.com",
            surface="postbox",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )
        domain = make_domain(self.tenant, "acme-mail.example")
        mailbox = make_mailbox(self.tenant, domain, local_part="alice")
        mailbox.status = MailboxStatus.ACTIVE
        mailbox.mail_engine_provisioned = True
        mailbox.save(update_fields=["status", "mail_engine_provisioned"])

        outsider = make_tenant(
            make_user("outside-owner@example.com"),
            name="Outside",
            slug="outside-custom",
        )
        outside_domain = make_domain(outsider, "outside-mail.example")
        outside_mailbox = make_mailbox(
            outsider, outside_domain, local_part="mallory"
        )
        outside_mailbox.status = MailboxStatus.ACTIVE
        outside_mailbox.mail_engine_provisioned = True
        outside_mailbox.save(update_fields=["status", "mail_engine_provisioned"])
        cache.clear()

        denied = APIClient().post(
            "/api/auth/login/",
            {"email": self.owner.email, "password": TEST_PASSWORD},
            format="json",
            HTTP_HOST=host.hostname,
        )
        self.assertEqual(denied.status_code, 404)

        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True):
            allowed = APIClient().post(
                "/api/postbox/auth/login/",
                {"email": mailbox.email, "password": "Mailbox-Password"},
                format="json",
                HTTP_HOST=host.hostname,
            )
            blocked = APIClient().post(
                "/api/postbox/auth/login/",
                {"email": outside_mailbox.email, "password": "Mailbox-Password"},
                format="json",
                HTTP_HOST=host.hostname,
            )

        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(blocked.status_code, 401)

    def test_custom_hub_refresh_cookie_is_host_only_and_strict(self):
        hub = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="hub.customer.com",
            surface="hub",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )
        cache.clear()

        response = APIClient().post(
            "/api/auth/login/",
            {"email": self.owner.email, "password": TEST_PASSWORD},
            format="json",
            HTTP_HOST=hub.hostname,
        )

        self.assertEqual(response.status_code, 200)
        cookie = response.cookies[REFRESH_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["domain"], "")
        self.assertEqual(cookie["path"], "/api/auth/")
        self.assertEqual(cookie["samesite"], "Strict")

    def test_custom_hub_rejects_refresh_token_for_another_tenant(self):
        other_owner = make_user("other-refresh@example.com")
        other = make_tenant(
            other_owner,
            name="Other Refresh",
            slug="other-refresh",
        )
        hub = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="hub.customer.com",
            surface="hub",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )
        cache.clear()

        tokens = make_tokens(other_owner, tenant_id=other.id)
        client = APIClient()
        client.cookies[REFRESH_COOKIE_NAME] = tokens["refresh"]
        response = client.post(
            "/api/auth/refresh/",
            {},
            format="json",
            HTTP_HOST=hub.hostname,
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn(REFRESH_COOKIE_NAME, response.cookies)
        self.assertEqual(response.cookies[REFRESH_COOKIE_NAME].value, "")

    def test_custom_postbox_cookie_is_host_only_host_prefix_cookie(self):
        host = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="inbox-cookie.customer.com",
            surface="postbox",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )
        domain = make_domain(self.tenant, "cookie-mail.example")
        mailbox = make_mailbox(self.tenant, domain, local_part="cookie")
        mailbox.status = MailboxStatus.ACTIVE
        mailbox.mail_engine_provisioned = True
        mailbox.save(update_fields=["status", "mail_engine_provisioned"])
        cache.clear()

        with (
            mock.patch("apps.postbox.auth.imap.authenticate", return_value=True),
            mock.patch("apps.postbox.imap.open_mailbox", side_effect=RuntimeError("skip folders")),
        ):
            response = APIClient().post(
                "/api/postbox/auth/login/",
                {"email": mailbox.email, "password": "Mailbox-Password"},
                format="json",
                HTTP_HOST=host.hostname,
            )

        self.assertEqual(response.status_code, 200)
        cookie = response.cookies[postbox_auth.SESSION_COOKIE_NAME]
        self.assertTrue(postbox_auth.SESSION_COOKIE_NAME.startswith("__Host-"))
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["domain"], "")
        self.assertEqual(cookie["path"], "/")
        self.assertEqual(cookie["samesite"], "Lax")

    def test_custom_hub_signup_is_not_a_new_workspace_entry_point(self):
        hub = CustomHostname.objects.create(
            tenant=self.tenant,
            hostname="hub.customer.com",
            surface="hub",
            dns_status=CustomHostnameDNSStatus.VERIFIED,
            provisioning_status=CustomHostnameProvisioningStatus.ACTIVE,
            certificate_status=CustomHostnameCertificateStatus.ACTIVE,
        )
        cache.clear()
        response = APIClient().post(
            "/api/auth/signup/",
            {
                "email": "new-user@example.com",
                "password": TEST_PASSWORD,
                "full_name": "New User",
                "workspace_name": "Wrong Workspace",
            },
            format="json",
            HTTP_HOST=hub.hostname,
        )
        self.assertEqual(response.status_code, 404)
