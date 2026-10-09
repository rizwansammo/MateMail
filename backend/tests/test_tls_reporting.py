"""P4-C.E isolated TLS-RPT tests; no real IMAP, DNS or production changes."""
import gzip
import json
from contextlib import contextmanager
from datetime import timedelta
from email.message import EmailMessage
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.tls_reports.parser import InvalidTlsReport, parse_json, unpack
from apps.tls_reports.ingest import ingest_mailbox_once, ingest_raw_message
from apps.tls_reports.models import TlsAggregateReport, TlsFailureBucket, TlsIngestCursor
from apps.tls_reports.services import store_policy, prune_old_reports
from apps.transport_security.models import DomainTransportSecurity
from tests.factories import (
    FAST_PASSWORD_HASHERS, auth_client, disable_throttling,
    make_domain, make_tenant, make_unverified_domain, make_user,
)


def report_json(domain="a.test", *, count=3, failed=2, extra_policies=None):
    start = timezone.now() - timedelta(days=1)
    end = timezone.now()
    policy = {
        "policy": {"policy-type": "sts", "policy-domain": domain, "mx-host": ["mx.matemail.pro"]},
        "summary": {"total-successful-session-count": count,
                    "total-failure-session-count": failed},
        "failure-details": [{
            "result-type": "certificate-host-mismatch",
            "failed-session-count": failed,
            "sending-mta-ip": "198.51.100.10",
            "receiving-ip": "192.0.2.33",
            "additional-information": "not for customer storage",
        }] if failed else [],
    }
    return json.dumps({
        "organization-name": "Example Reporter", "report-id": "daily-001",
        "contact-info": "mailto:reporter@example.test",
        "date-range": {"start-datetime": start.isoformat(),
                       "end-datetime": end.isoformat()},
        "policies": [policy] + (extra_policies or []),
    }).encode()


def report_mime(raw, *, filename="report.json.gz", mime="tlsrpt+gzip"):
    msg = EmailMessage()
    msg["To"] = "tlsrpt@mail.matemail.pro"
    msg["From"] = "reporter@example.test"
    msg.set_content("Report attached")
    msg.add_attachment(raw, maintype="application", subtype=mime, filename=filename)
    return msg.as_bytes()


class ParserSecurityTest(SimpleTestCase):
    def test_valid_json_gzip_and_no_sensitive_fields_saved(self):
        raw = report_json()
        parsed = parse_json(raw)
        self.assertEqual(parsed[0].domain, "a.test")
        self.assertEqual(parsed[0].successes, 3)
        self.assertEqual(parsed[0].failures, 2)
        self.assertEqual(parsed[0].buckets, (("certificate-host-mismatch", 2),))
        self.assertEqual(unpack(gzip.compress(raw), "daily.json.gz", "application/tlsrpt+gzip"), raw)
        self.assertEqual(unpack(raw, "daily.json", "application/tlsrpt+json"), raw)

    def test_poison_json_and_oversized_payloads_rejected(self):
        raw = report_json()
        invalid = [
            b'{"organization-name":"first","organization-name":"duplicate"}',
            b'{"policies": NaN}', b'{"policies": false}',
            report_json(domain="../other"),
            report_json(count=True),
            report_json(failed=-1),
            report_json(failed=0).replace(b'"report-id":', b'"unknown-id":'),
            raw + b"trailing",
        ]
        for sample in invalid:
            with self.subTest(example=sample[:45]), self.assertRaises(InvalidTlsReport):
                parse_json(sample)
        with self.assertRaises(InvalidTlsReport):
            unpack(gzip.compress(b"X" * (2 * 1024 * 1024 + 32)), "bad.json.gz", "application/tlsrpt+gzip")
        with self.assertRaises(InvalidTlsReport):
            unpack(raw, "anything.zip", "application/zip")

    @override_settings(TLS_RPT_INGEST_ENABLED=False)
    def test_default_off_never_opens_mailbox(self):
        with mock.patch("apps.tls_reports.ingest.open_mailbox") as opened:
            self.assertEqual(ingest_mailbox_once(), {"disabled": 1})
            opened.assert_not_called()


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class TenantTlsReportingTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner_a = make_user("tls-a@example.test")
        self.owner_b = make_user("tls-b@example.test")
        self.tenant_a = make_tenant(self.owner_a, "TLS A", "tls-a")
        self.tenant_b = make_tenant(self.owner_b, "TLS B", "tls-b")
        self.domain_a = make_domain(self.tenant_a, "a.test")
        self.domain_b = make_domain(self.tenant_b, "b.test")
        self.client_a = auth_client(self.owner_a, self.tenant_a)
        self.client_b = auth_client(self.owner_b, self.tenant_b)
        for domain in (self.domain_a, self.domain_b):
            domain.ownership_verified_at = timezone.now() - timedelta(days=5)
            domain.save(update_fields=["ownership_verified_at"])
            config = DomainTransportSecurity.objects.create(domain=domain, enabled=True)
            DomainTransportSecurity.objects.filter(pk=config.pk).update(
                created_at=timezone.now() - timedelta(days=5)
            )

    def test_isolated_storage_and_duplicate_detection(self):
        raw = report_json()
        parsed = parse_json(raw)[0]
        self.assertEqual(store_policy(parsed).status, "stored")
        self.assertEqual(store_policy(parsed).status, "duplicate")
        self.assertEqual(TlsAggregateReport.objects.count(), 1)
        row = TlsAggregateReport.objects.get()
        self.assertEqual(row.tenant, self.tenant_a)
        self.assertEqual(row.domain, self.domain_a)
        self.assertEqual(row.successful_sessions, 3)
        self.assertEqual(TlsFailureBucket.objects.count(), 1)
        fields = [field.name for field in row._meta.fields]
        for forbidden in ("raw_json", "receiving_ip", "sending_mta_ip", "additional_information", "report_body"):
            self.assertNotIn(forbidden, fields)

    def test_api_scoping_and_validation(self):
        store_policy(parse_json(report_json())[0])
        url = f"/api/domains/{self.domain_a.pk}/tls-reports/"
        resp = self.client_a.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["successful_sessions"], 3)
        self.assertEqual(resp.data["failed_sessions"], 2)
        self.assertEqual(resp.data["failure_types"][0]["result_type"], "certificate-host-mismatch")
        self.assertEqual(self.client_b.get(url).status_code, 404)
        self.assertEqual(self.client_b.get(f"/api/domains/{self.domain_b.pk}/tls-reports/").data["report_count"], 0)
        self.assertEqual(self.client_a.get(url + "?days=999").status_code, 400)

    def test_preownership_unverified_and_optout_do_not_expose(self):
        make_unverified_domain(self.tenant_b, "pending.test")
        self.assertEqual(store_policy(parse_json(report_json("pending.test"))[0]).status, "unmanaged")
        self.assertEqual(store_policy(parse_json(report_json("unknown.test"))[0]).status, "unmanaged")
        DomainTransportSecurity.objects.filter(domain=self.domain_a).update(enabled=False)
        self.assertEqual(store_policy(parse_json(report_json("a.test"))[0]).status, "unmanaged")
        self.assertFalse(TlsAggregateReport.objects.exists())

    def test_preownership_report_is_not_attributed(self):
        self.domain_a.ownership_verified_at = timezone.now()
        self.domain_a.save(update_fields=["ownership_verified_at"])
        self.assertEqual(store_policy(parse_json(report_json())[0]).status, "unmanaged")

    def test_multi_policy_document_never_leaks_tenants(self):
        raw = json.loads(report_json())
        another = dict(raw["policies"][0])
        another["policy"] = dict(another["policy"], **{"policy-domain": "b.test"})
        raw["policies"].append(another)
        policies = parse_json(json.dumps(raw).encode())
        self.assertEqual([store_policy(p).status for p in policies], ["stored", "stored"])
        self.assertEqual(self.client_a.get(f"/api/domains/{self.domain_a.pk}/tls-reports/").data["report_count"], 1)
        self.assertEqual(self.client_b.get(f"/api/domains/{self.domain_b.pk}/tls-reports/").data["report_count"], 1)

    def test_mime_and_retention(self):
        payload = report_mime(gzip.compress(report_json()))
        self.assertEqual(ingest_raw_message(payload)["stored"], 1)
        self.assertEqual(ingest_raw_message(payload)["duplicate"], 1)
        TlsAggregateReport.objects.update(created_at=timezone.now() - timedelta(days=100))
        self.assertGreater(prune_old_reports(), 0)
        self.assertEqual(TlsFailureBucket.objects.count(), 0)

    @override_settings(TLS_RPT_INGEST_ENABLED=True)
    def test_readonly_cursor_rejected_message_and_uidvalidity(self):
        raw = report_mime(gzip.compress(report_json()))
        class FakeMailbox:
            uidvalidity = 5
            def select(self, folder, *, readonly=False):
                assert folder == "INBOX" and readonly
                return SimpleNamespace(uid_validity=self.uidvalidity)
            def search_uids(self, *args, **kwargs):
                return [1, 2]
            def fetch_summaries(self, uids):
                return [SimpleNamespace(size=len(raw))]
            def fetch_raw(self, uid):
                return raw if uid == 1 else b"unsupported"
            def store_flags(self, *args, **kwargs):
                raise AssertionError("MUST NOT mutate flags")
            def move(self, *args, **kwargs):
                raise AssertionError("MUST NOT move mail")
        box = FakeMailbox()
        @contextmanager
        def opened(addr):
            self.assertEqual(addr, "tlsrpt@mail.matemail.pro")
            yield box
        with mock.patch("apps.tls_reports.ingest.open_mailbox", opened):
            one = ingest_mailbox_once()
            self.assertEqual(one["stored"], 1)
            self.assertEqual(one["rejected"], 1)
            self.assertEqual(one["processed"], 2)
            cursor = TlsIngestCursor.objects.get()
            self.assertEqual(cursor.last_uid, 2)
            self.assertEqual(cursor.rejected_count, 1)
            self.assertEqual(ingest_mailbox_once()["processed"], 0)
            box.uidvalidity = 6
            self.assertEqual(ingest_mailbox_once()["duplicate"], 1)

    @override_settings(
        TLS_RPT_INGEST_ENABLED=True,
        TLS_RPT_DNS_PUBLICATION_ENABLED=True,
        TLS_RPT_RECEIVER_VERIFIED=True,
    )
    def test_tls_publication_is_separately_operator_gated(self):
        from apps.transport_security.serializers import describe_transport_security
        config = DomainTransportSecurity.objects.get(domain=self.domain_a)
        enabled = describe_transport_security(self.domain_a, config)
        self.assertTrue(enabled["dns_records"][2]["publish_ready"])
        self.assertFalse(enabled["can_publish_dns"])
        with override_settings(TLS_RPT_RECEIVER_VERIFIED=False):
            blocked = describe_transport_security(self.domain_a, config)
            self.assertFalse(blocked["dns_records"][2]["publish_ready"])
        from apps.domains.models import DomainOwnership
        self.domain_a.ownership_status = DomainOwnership.PENDING
        self.domain_a.save(update_fields=["ownership_status"])
        lost = describe_transport_security(self.domain_a, config)
        self.assertFalse(lost["dns_records"][2]["publish_ready"])

    @override_settings(TLS_RPT_INGEST_ENABLED=False)
    def test_platform_health_is_disabled_by_default(self):
        # Do not incorrectly claim a working report receiver when disabled.
        from apps.tls_reports.tasks import poll_mailbox
        self.assertEqual(poll_mailbox(), {"disabled": 1})
