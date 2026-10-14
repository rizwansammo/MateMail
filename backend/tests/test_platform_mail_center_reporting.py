"""Release 4 Phase 6E: privacy-bound Platform Mail Center report dashboard."""
import hashlib
import json
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.dmarc_reports.models import AggregateReport, IngestCursor
from apps.tls_reports.models import TlsAggregateReport, TlsIngestCursor
from apps.platform_admin.models import PlatformAuditLog
from tests.factories import (
    FAST_PASSWORD_HASHERS, auth_client, disable_throttling,
    make_domain, make_tenant, make_user,
)

URL = "/api/platform/mail-center/reports/overview/"


def sha(label):
    return hashlib.sha256(label.encode()).hexdigest()


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    PLATFORM_MAIL_CENTER_ENABLED=True,
    PLATFORM_MAIL_CENTER_REPORTS_ENABLED=True,
    DMARC_REPORT_INGEST_ENABLED=True,
    TLS_RPT_INGEST_ENABLED=True,
    DMARC_REPORT_ADDRESS="dmarc@system.matemail.pro",
    TLS_RPT_REPORT_ADDRESS="tlsrpt@system.matemail.pro",
    DMARC_REPORT_DUAL_RECEIVER_ENABLED=True,
    TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=True,
)
class PlatformMailCenterReportTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("overview-platform-admin@example.test", is_platform_admin=True)
        self.staff = make_user("overview-nonplatform-staff@example.test", is_staff=True)
        self.owner_a = make_user("tenant-overview-a@example.test")
        self.owner_b = make_user("tenant-overview-b@example.test")
        self.tenant_a = make_tenant(self.owner_a, slug="overview-tenant-a")
        self.tenant_b = make_tenant(self.owner_b, slug="overview-tenant-b")
        self.domain_a = make_domain(self.tenant_a, "sensitive-client-a.example")
        self.domain_b = make_domain(self.tenant_b, "sensitive-client-b.example")
        self.admin_client = auth_client(self.admin)
        self.client_a = auth_client(self.owner_a, self.tenant_a)
        self.staff_client = auth_client(self.staff)
        self.now = timezone.now()
        self._make_dmarc("platform-old", "mail.matemail.pro", 15, 9)
        self._make_dmarc("platform-new", "system.matemail.pro", 20, 10, hours=24)
        self._make_dmarc(
            "tenant-leak", "sensitive-client-a.example", 999, 999,
            tenant=self.tenant_a, domain=self.domain_a,
        )
        # Even a corrupt/misattributed platform-domain row with a tenant set
        # must not appear in the platform DMARC scope.
        self._make_dmarc(
            "misattributed-platform-label", "mail.matemail.pro", 777, 777,
            tenant=self.tenant_a, domain=self.domain_a,
        )
        # Platform-owned but unlisted domain cannot appear either.
        self._make_dmarc("unlisted-platform", "another-sender.example", 888, 888)
        self._make_dmarc("old-platform-report", "system.matemail.pro", 111, 111, hours=40 * 24)
        self._make_tls("tenant-one", self.tenant_a, self.domain_a, 42, 3)
        self._make_tls("tenant-two", self.tenant_b, self.domain_b, 11, 2, hours=24)
        self._make_tls("old-tenant", self.tenant_a, self.domain_a, 600, 500, hours=40 * 24)

    def _make_dmarc(self, label, name, messages, passes, *, hours=1, tenant=None, domain=None):
        end = self.now - timedelta(hours=hours)
        return AggregateReport.objects.create(
            tenant=tenant, domain=domain, policy_domain=name,
            reporter="confidential-reporter-" + label + ".example",
            report_identifier="hidden-" + label,
            xml_sha256=sha("dmarc:" + label),
            period_start=end - timedelta(hours=1), period_end=end,
            message_count=messages, spf_pass=passes, dkim_pass=passes,
            dmarc_pass=passes,
        )

    def _make_tls(self, label, tenant, domain, success, failure, *, hours=1):
        end = self.now - timedelta(hours=hours)
        return TlsAggregateReport.objects.create(
            tenant=tenant, domain=domain,
            policy_domain=domain.domain,
            reporter="confidential-tls-reporter-" + label + ".example",
            report_id="secret-report-id-" + label,
            policy_type="sts",
            json_sha256=sha("tls:" + label),
            period_start=end - timedelta(hours=1), period_end=end,
            successful_sessions=success, failed_sessions=failure,
        )

    def test_platform_only_and_anonymous_denied(self):
        for client in (APIClient(), self.client_a, self.staff_client):
            response = client.get(URL)
            self.assertIn(response.status_code, (401, 403))
        self.assertFalse(PlatformAuditLog.objects.exists())

    def test_reporting_gate_disabled_independently_and_no_queries(self):
        with override_settings(PLATFORM_MAIL_CENTER_REPORTS_ENABLED=False):
            self.assertEqual(self.admin_client.get(URL).status_code, 503)
        with override_settings(PLATFORM_MAIL_CENTER_ENABLED=False):
            self.assertEqual(self.admin_client.get(URL).status_code, 503)
        self.assertFalse(PlatformAuditLog.objects.exists())

    def test_dashboard_never_opens_imap_or_requires_master_credential(self):
        with override_settings(POSTBOX_MASTER_PASSWORD=""):
            with mock.patch("apps.postbox.imap.open_mailbox") as mailbox:
                response = self.admin_client.get(URL)
            mailbox.assert_not_called()
        self.assertEqual(response.status_code, 200, response.data)

    def test_dmarc_only_reserved_platform_sender_rows(self):
        response = self.admin_client.get(URL)
        self.assertEqual(response.status_code, 200, response.data)
        section = response.data["dmarc"]
        self.assertEqual(section["scope"], "platform_senders_only")
        self.assertEqual(section["report_count"], 2)
        self.assertEqual(section["message_count"], 35)
        self.assertEqual(section["spf_pass"], 19)
        self.assertEqual(section["dkim_pass"], 19)
        self.assertEqual(section["dmarc_pass"], 19)
        self.assertEqual(len(section["daily"]), 30)
        self.assertEqual(sum(r["message_count"] for r in section["daily"]), 35)

    def test_tls_only_global_aggregate_no_tenant_details(self):
        response = self.admin_client.get(URL, {"days": 7})
        self.assertEqual(response.status_code, 200, response.data)
        section = response.data["tls"]
        self.assertEqual(section["scope"], "all_tenants_aggregate_only")
        self.assertEqual(section["report_count"], 2)
        self.assertEqual(section["successful_sessions"], 53)
        self.assertEqual(section["failed_sessions"], 5)
        self.assertEqual(len(section["daily"]), 7)
        self.assertEqual(sum(p["failed_sessions"] for p in section["daily"]), 5)
        self.assertEqual(response.data["window"]["timezone"], "UTC")
        self.assertEqual(response.data["window"]["days"], 7)
        content = json.dumps(response.data, default=str)
        for sensitive in (
            "sensitive-client-a.example", "sensitive-client-b.example",
            "confidential-tls-reporter", "confidential-reporter",
            "secret-report-id", "203.0.113", "tenant_id",
            "domain_id", "report_identifier", "source_ip", "xml_sha256", "json_sha256",
        ):
            self.assertNotIn(sensitive, content)
        audit = PlatformAuditLog.objects.get(action="mail_center.reports_overview_read")
        self.assertEqual(audit.target_label, "platform_reporting")
        self.assertEqual(audit.metadata, {"days": 7})
        self.assertNotIn("sensitive-client", str(audit.metadata))

    def test_rejects_tenant_domain_and_row_selectors(self):
        for params in (
            "?domain=sensitive-client-a.example",
            "?tenant_id=" + str(self.tenant_a.pk),
            "?report_id=secret-report-id-tenant-one",
            "?days=0", "?days=91", "?days=garbage",
            "?days=7&days=30", "?days=7&domain=foo",
            "?days=-1", "?days=1.25",
        ):
            self.assertEqual(self.admin_client.get(URL + params).status_code, 400, params)
        self.assertFalse(PlatformAuditLog.objects.exists())

    def test_receiver_health_both_cursors_without_secrets(self):
        now = timezone.now()
        IngestCursor.objects.create(
            address="dmarc@mail.matemail.pro", uid_validity=17, last_uid=700,
            last_checked_at=now, last_success_at=now,
            last_error="PRIVATE SECRET MUST NOT BE EXPOSED",
        )
        IngestCursor.objects.create(
            address="dmarc@system.matemail.pro", last_checked_at=now - timedelta(hours=3),
            last_success_at=now - timedelta(hours=3),
        )
        TlsIngestCursor.objects.create(
            address="tlsrpt@mail.matemail.pro", last_checked_at=now,
            last_success_at=now, processed_count=100, rejected_count=5,
        )
        overview = self.admin_client.get(URL)
        self.assertEqual(overview.status_code, 200)
        receivers = overview.data["receivers"]
        self.assertEqual([x["status"] for x in receivers["dmarc"]], ["operational", "stale"])
        self.assertEqual([x["status"] for x in receivers["tls"]], ["operational", "waiting"])
        self.assertTrue(all(item["active"] for row in receivers.values() for item in row))
        text = json.dumps(overview.data, default=str)
        self.assertNotIn("PRIVATE SECRET", text)
        self.assertNotIn("uid_validity", text)
        self.assertNotIn("last_uid", text)
        self.assertNotIn("processed_count", text)
        self.assertNotIn("rejected_count", text)

    def test_receiver_disabled_and_bad_address_fail_closed(self):
        with override_settings(
            DMARC_REPORT_ADDRESS="attacker@external.example",
            DMARC_REPORT_DUAL_RECEIVER_ENABLED=False,
            TLS_RPT_INGEST_ENABLED=False,
        ):
            result = self.admin_client.get(URL)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            [x["status"] for x in result.data["receivers"]["dmarc"]],
            ["misconfigured", "misconfigured"],
        )
        self.assertEqual(
            [x["status"] for x in result.data["receivers"]["tls"]],
            ["disabled", "disabled"],
        )
        self.assertNotIn("attacker@external.example", json.dumps(result.data, default=str))

    def test_empty_states_and_one_day_calendar(self):
        AggregateReport.objects.all().delete()
        TlsAggregateReport.objects.all().delete()
        result = self.admin_client.get(URL, {"days": "1"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["dmarc"]["report_count"], 0)
        self.assertEqual(result.data["tls"]["report_count"], 0)
        self.assertEqual(result.data["tls"]["failed_sessions"], 0)
        self.assertEqual(result.data["dmarc"]["daily"][0]["message_count"], 0)
        self.assertEqual(len(result.data["tls"]["daily"]), 1)

    def test_get_only_and_never_change_cursor(self):
        IngestCursor.objects.create(address="dmarc@mail.matemail.pro", last_uid=11)
        first = self.admin_client.get(URL)
        self.assertEqual(first.status_code, 200)
        self.assertIn("no-store", first["Cache-Control"])
        self.assertEqual(self.admin_client.post(URL, {}, format="json").status_code, 405)
        self.assertEqual(self.admin_client.delete(URL).status_code, 405)
        self.assertEqual(IngestCursor.objects.get().last_uid, 11)
