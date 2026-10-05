from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.tenants.models import (
    CustomHostname,
    CustomHostnameCertificateStatus,
    CustomHostnameDNSStatus,
    CustomHostnameProvisioningStatus,
    MemberRole,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    add_member,
    auth_client,
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

    def test_pending_queue_contains_only_dns_verified_unprovisioned_rows(self):
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
        self.auth()

        response = self.client.get("/api/internal/custom-hostnames/pending/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["hostname"], "mail.customer.com")
        self.assertEqual(
            response.data["results"][0]["cname_target"],
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

    def test_worker_can_advance_to_ready_but_cannot_activate(self):
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

        response = self.client.post(
            f"/api/internal/custom-hostnames/{self.row.id}/state/",
            {
                "provisioning_status": "active",
                "certificate_status": "active",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)


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
        self.assertEqual(response.status_code, 401)

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
