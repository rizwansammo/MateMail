"""P4-B security and tenant-isolation regression tests. No real IMAP/DNS."""
from datetime import timedelta
from email.message import EmailMessage
from unittest import mock
import gzip
import io
import zipfile

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.dmarc_reports.ingest import ingest_mailbox_once, ingest_raw_message
from apps.dmarc_reports.models import AggregateReport, AggregateRecord, IngestCursor
from apps.dmarc_reports.parser import InvalidReport, extract_xml_documents, parse_xml
from apps.dmarc_reports.services import store_report, prune_old_reports
from tests.factories import (
    auth_client, disable_throttling, make_domain, make_tenant, make_user,
    make_unverified_domain, FAST_PASSWORD_HASHERS,
)


def report_xml(domain="acme.test", ip="203.0.113.9", count=4, report_id="r1"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feedback>
  <version>1.0</version>
  <report_metadata><org_name>Trusted Example</org_name>
    <email>dmarc@example.test</email><report_id>{report_id}</report_id>
    <date_range><begin>1791331200</begin><end>1791417600</end></date_range>
  </report_metadata>
  <policy_published><domain>{domain}</domain><adkim>r</adkim>
    <aspf>r</aspf><p>none</p></policy_published>
  <record><row><source_ip>{ip}</source_ip><count>{count}</count>
    <policy_evaluated><disposition>none</disposition>
      <dkim>pass</dkim><spf>fail</spf></policy_evaluated>
  </row><identifiers><header_from>{domain}</header_from></identifiers></record>
</feedback>""".encode()


def mail_with_attachment(payload, *, fname="aggregate.xml", subtype="xml"):
    msg = EmailMessage()
    msg["From"] = "reporter@example.test"
    msg["To"] = "dmarc@mail.matemail.pro"
    msg["Subject"] = "Aggregate report"
    msg.set_content("DMARC report attached.")
    msg.add_attachment(payload, maintype="application", subtype=subtype, filename=fname)
    return msg.as_bytes()


class DmarcParserSafetyTest(SimpleTestCase):
    def test_normal_dmarc_report_and_counts(self):
        r = parse_xml(report_xml())
        self.assertEqual(r.policy_domain, "acme.test")
        self.assertEqual(r.count, 4)
        self.assertEqual(r.spf_pass, 0)
        self.assertEqual(r.dkim_pass, 4)
        self.assertEqual(r.dmarc_pass, 4)
        self.assertEqual(r.rows[0].source_ip, "203.0.113.9")

    def test_unsafe_xml_entities_and_dtd_rejected(self):
        for xml in [
            b'<!DOCTYPE feedback [<!ENTITY x SYSTEM "file:///etc/passwd">]><feedback>&x;</feedback>',
            b'<!DOCTYPE feedback [<!ENTITY x "billion">]><feedback>&x;</feedback>',
        ]:
            with self.subTest(xml=xml[:30]), self.assertRaises(InvalidReport):
                parse_xml(xml)

    def test_missing_invalid_date_domain_and_record_count_rejected(self):
        for xml in [
            report_xml(domain="bad/host"),
            report_xml(ip="not-an-ip"),
            report_xml(count="-1"),
            report_xml(count="100000000000"),
            report_xml().replace(b"<end>1791417600</end>", b"<end>1700000000</end>"),
            report_xml().replace(b"<record>", b"").replace(b"</record>", b""),
        ]:
            with self.subTest(xml=xml[:90]), self.assertRaises(InvalidReport):
                parse_xml(xml)

    def test_namespace_aware_report(self):
        xml = report_xml().replace(b"<feedback>", b'<feedback xmlns="urn:ietf:params:xml:ns:dmarc">').replace(b"</feedback>", b"</feedback>")
        self.assertEqual(parse_xml(xml).policy_domain, "acme.test")

    def test_gzip_and_zip_bounded(self):
        xml = report_xml()
        self.assertEqual(extract_xml_documents(gzip.compress(xml), "report.xml.gz"), [xml])
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("record.xml", xml)
        self.assertEqual(extract_xml_documents(stream.getvalue(), "report.zip"), [xml])
        with self.assertRaises(InvalidReport):
            extract_xml_documents(gzip.compress(b"x" * (2 * 1024 * 1024 + 20)), "big.xml.gz")
        with self.assertRaises(InvalidReport):
            extract_xml_documents(b"x" * (4 * 1024 * 1024 + 1), "big.xml")

    def test_zip_path_traversal_rejected(self):
        f = io.BytesIO()
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("../evil.xml", report_xml())
        with self.assertRaises(InvalidReport):
            extract_xml_documents(f.getvalue(), "malicious.zip")

    @override_settings(DMARC_REPORT_INGEST_ENABLED=False)
    def test_polling_disabled_has_no_mailbox_activity(self):
        with mock.patch("apps.dmarc_reports.ingest.open_mailbox") as opened:
            self.assertEqual(ingest_mailbox_once(), {"disabled": 1})
        opened.assert_not_called()


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class DmarcTenantIsolationTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner_a = make_user("a-report@test.example")
        self.owner_b = make_user("b-report@test.example")
        self.tenant_a = make_tenant(self.owner_a, "A", "a-dmarc")
        self.tenant_b = make_tenant(self.owner_b, "B", "b-dmarc")
        self.domain_a = make_domain(self.tenant_a, "a.test")
        self.domain_b = make_domain(self.tenant_b, "b.test")
        self.client_a = auth_client(self.owner_a, self.tenant_a)
        self.client_b = auth_client(self.owner_b, self.tenant_b)

    def test_verified_domain_import_is_deduplicated_and_never_stores_raw_xml(self):
        parsed = parse_xml(report_xml("a.test"))
        self.assertEqual(store_report(parsed).status, "stored")
        self.assertEqual(store_report(parsed).status, "duplicate")
        self.assertEqual(AggregateReport.objects.count(), 1)
        row = AggregateReport.objects.get()
        self.assertEqual(row.domain, self.domain_a)
        self.assertEqual(row.tenant, self.tenant_a)
        self.assertEqual(row.message_count, 4)
        self.assertEqual(AggregateRecord.objects.count(), 1)
        self.assertFalse(any("xml" in f.name and f.name != "xml_sha256" for f in AggregateReport._meta.fields))

    def test_unverified_or_unknown_domains_cannot_be_bound_to_any_tenant(self):
        make_unverified_domain(self.tenant_b, "pending.test")
        self.assertEqual(store_report(parse_xml(report_xml("pending.test"))).status, "unmanaged")
        self.assertEqual(store_report(parse_xml(report_xml("missing.test"))).status, "unmanaged")
        self.assertFalse(AggregateReport.objects.exists())

    def test_cross_tenant_domain_lookup_is_404_and_admin_only(self):
        store_report(parse_xml(report_xml("a.test")))
        url_a = f"/api/domains/{self.domain_a.pk}/dmarc-reports/"
        url_b = f"/api/domains/{self.domain_b.pk}/dmarc-reports/"
        own = self.client_a.get(url_a)
        self.assertEqual(own.status_code, 200)
        self.assertEqual(own.data["message_count"], 4)
        self.assertEqual(own.data["top_sources"][0]["ip"], "203.0.113.9")
        self.assertEqual(self.client_b.get(url_a).status_code, 404)
        self.assertEqual(self.client_b.get(url_b).data["message_count"], 0)
        self.assertEqual(self.client_a.get(url_a + "?days=999").status_code, 400)

    def test_aggregate_mime_ingestion_with_duplicate(self):
        mime = mail_with_attachment(report_xml("a.test"))
        self.assertEqual(ingest_raw_message(mime)["stored"], 1)
        self.assertEqual(ingest_raw_message(mime)["duplicate"], 1)

    def test_platform_own_sender_is_not_in_tenant_reports(self):
        platform = store_report(parse_xml(report_xml("mail.matemail.pro")))
        self.assertEqual(platform.status, "stored")
        obj = AggregateReport.objects.get()
        self.assertIsNone(obj.tenant)
        self.assertIsNone(obj.domain)
        self.assertEqual(self.client_a.get(f"/api/domains/{self.domain_a.pk}/dmarc-reports/").data["message_count"], 0)

    @override_settings(DMARC_REPORT_RETENTION_DAYS=90)
    def test_retention_prunes_summary_and_rows_only(self):
        store_report(parse_xml(report_xml("a.test")))
        obj = AggregateReport.objects.get()
        AggregateReport.objects.filter(pk=obj.pk).update(created_at=timezone.now() - timedelta(days=100))
        self.assertGreater(prune_old_reports(), 0)
        self.assertFalse(AggregateRecord.objects.exists())

    @override_settings(DMARC_REPORT_INGEST_ENABLED=False)
    def test_disabled_ingest_never_creates_cursor(self):
        self.assertEqual(ingest_mailbox_once(), {"disabled": 1})
        self.assertEqual(IngestCursor.objects.count(), 0)
