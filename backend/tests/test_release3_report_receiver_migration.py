"""Release 3 report receivers: safe dual read, no DNS or sender side effects."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings

from apps.dmarc_reports.ingest import (
    configured_receiver_addresses as dmarc_addresses,
    ingest_mailbox_once as poll_dmarc_address,
)
from apps.tls_reports.ingest import (
    configured_receiver_addresses as tls_addresses,
    ingest_mailbox_once as poll_tls_address,
)
from apps.dmarc_reports.models import IngestCursor
from apps.tls_reports.models import TlsIngestCursor


class ReportReceiverMigrationTests(TestCase):
    @override_settings(
        DMARC_REPORT_ADDRESS="dmarc@mail.matemail.pro",
        TLS_RPT_REPORT_ADDRESS="tlsrpt@mail.matemail.pro",
        DMARC_REPORT_DUAL_RECEIVER_ENABLED=False,
        TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=False,
    )
    def test_default_is_only_original_receiver(self):
        self.assertEqual(dmarc_addresses(), ("dmarc@mail.matemail.pro",))
        self.assertEqual(tls_addresses(), ("tlsrpt@mail.matemail.pro",))

    @override_settings(
        DMARC_REPORT_ADDRESS="dmarc@mail.matemail.pro",
        TLS_RPT_REPORT_ADDRESS="tlsrpt@mail.matemail.pro",
        DMARC_REPORT_DUAL_RECEIVER_ENABLED=True,
        TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=True,
    )
    def test_dual_mode_exactly_two_platform_addresses(self):
        self.assertEqual(
            dmarc_addresses(), ("dmarc@mail.matemail.pro", "dmarc@system.matemail.pro")
        )
        self.assertEqual(
            tls_addresses(), ("tlsrpt@mail.matemail.pro", "tlsrpt@system.matemail.pro")
        )

    @override_settings(
        DMARC_REPORT_INGEST_ENABLED=True,
        TLS_RPT_INGEST_ENABLED=True,
        DMARC_REPORT_DUAL_RECEIVER_ENABLED=False,
        TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=False,
        DMARC_REPORT_ADDRESS="dmarc@mail.matemail.pro",
        TLS_RPT_REPORT_ADDRESS="tlsrpt@mail.matemail.pro",
    )
    def test_new_address_fails_closed_when_dual_mode_disabled(self):
        with mock.patch("apps.dmarc_reports.ingest.open_mailbox") as dmarc, \
             mock.patch("apps.tls_reports.ingest.open_mailbox") as tls:
            with self.assertRaises(ValueError):
                poll_dmarc_address(address="dmarc@system.matemail.pro")
            with self.assertRaises(ValueError):
                poll_tls_address(address="tlsrpt@system.matemail.pro")
            dmarc.assert_not_called()
            tls.assert_not_called()
        self.assertFalse(IngestCursor.objects.exists())
        self.assertFalse(TlsIngestCursor.objects.exists())

    @override_settings(
        DMARC_REPORT_INGEST_ENABLED=True,
        TLS_RPT_INGEST_ENABLED=True,
        DMARC_REPORT_DUAL_RECEIVER_ENABLED=True,
        TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=True,
    )
    def test_customer_or_arbitrary_address_is_never_read(self):
        with mock.patch("apps.dmarc_reports.ingest.open_mailbox") as dmarc, \
             mock.patch("apps.tls_reports.ingest.open_mailbox") as tls:
            with self.assertRaises(ValueError):
                poll_dmarc_address(address="dmarc@customer.example")
            with self.assertRaises(ValueError):
                poll_tls_address(address="tlsrpt@customer.example")
            dmarc.assert_not_called()
            tls.assert_not_called()

    @override_settings(
        DMARC_REPORT_ADDRESS="dmarc@malicious.example",
        TLS_RPT_REPORT_ADDRESS="tlsrpt@malicious.example",
        DMARC_REPORT_DUAL_RECEIVER_ENABLED=True,
        TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=True,
    )
    def test_invalid_primary_configuration_denied_even_in_dual_mode(self):
        with self.assertRaises(ValueError):
            dmarc_addresses()
        with self.assertRaises(ValueError):
            tls_addresses()

    @override_settings(
        DMARC_REPORT_INGEST_ENABLED=True,
        TLS_RPT_INGEST_ENABLED=True,
        DMARC_REPORT_DUAL_RECEIVER_ENABLED=True,
        TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=True,
    )
    def test_read_only_receivers_use_distinct_cursor_rows(self):
        @contextmanager
        def opened(address):
            class EmptyMailbox:
                def select(self, folder, readonly=False):
                    assert folder == "INBOX" and readonly is True
                    return SimpleNamespace(uid_validity=100 if address.endswith("@mail.matemail.pro") else 200)

                def search_uids(self, criteria, newest=False):
                    assert criteria[0] == "UID" and newest is False
                    return []

            yield EmptyMailbox()

        with mock.patch("apps.dmarc_reports.ingest.open_mailbox", opened), \
             mock.patch("apps.tls_reports.ingest.open_mailbox", opened):
            for address in dmarc_addresses():
                self.assertEqual(poll_dmarc_address(address=address)["processed"], 0)
            for address in tls_addresses():
                self.assertEqual(poll_tls_address(address=address)["processed"], 0)
        self.assertEqual(
            set(IngestCursor.objects.values_list("address", flat=True)),
            set(dmarc_addresses()),
        )
        self.assertEqual(
            set(TlsIngestCursor.objects.values_list("address", flat=True)),
            set(tls_addresses()),
        )
        self.assertEqual(
            IngestCursor.objects.get(address="dmarc@mail.matemail.pro").uid_validity, 100
        )
        self.assertEqual(
            IngestCursor.objects.get(address="dmarc@system.matemail.pro").uid_validity, 200
        )

    @override_settings(
        DMARC_REPORT_INGEST_ENABLED=True,
        TLS_RPT_INGEST_ENABLED=True,
        DMARC_REPORT_DUAL_RECEIVER_ENABLED=True,
        TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=True,
    )
    def test_poll_tasks_hold_one_lock_and_aggregate(self):
        from apps.dmarc_reports.tasks import poll_mailbox as poll_dmarc
        from apps.tls_reports.tasks import poll_mailbox as poll_tls
        for module, task, expected in (
            ("apps.dmarc_reports.tasks", poll_dmarc, dmarc_addresses()),
            ("apps.tls_reports.tasks", poll_tls, tls_addresses()),
        ):
            with self.subTest(module=module):
                with mock.patch(module + ".cache.add", return_value=True), \
                     mock.patch(module + ".cache.delete") as released, \
                     mock.patch(module + ".ingest_mailbox_once", return_value={"processed": 1, "stored": 1}) as read:
                    self.assertEqual(task(), {"processed": 2, "stored": 2})
                    self.assertEqual([c.kwargs["address"] for c in read.call_args_list], list(expected))
                    released.assert_called_once()

    @override_settings(
        DMARC_REPORT_INGEST_ENABLED=False,
        TLS_RPT_INGEST_ENABLED=False,
        DMARC_REPORT_DUAL_RECEIVER_ENABLED=True,
        TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=True,
    )
    def test_ingestion_disabled_still_does_not_touch_mailboxes(self):
        from apps.dmarc_reports.tasks import poll_mailbox as poll_dmarc
        from apps.tls_reports.tasks import poll_mailbox as poll_tls
        with mock.patch("apps.dmarc_reports.ingest.open_mailbox") as dmarc, \
             mock.patch("apps.tls_reports.ingest.open_mailbox") as tls:
            self.assertEqual(poll_dmarc(), {"disabled": 1})
            self.assertEqual(poll_tls(), {"disabled": 1})
            dmarc.assert_not_called()
            tls.assert_not_called()
