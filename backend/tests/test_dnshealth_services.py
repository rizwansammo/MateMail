from django.test import SimpleTestCase

from apps.dnshealth.models import DNSCheckStatus
from apps.dnshealth.services import _evaluate_txt


class TxtEvaluationTest(SimpleTestCase):
    def test_unrelated_root_txt_does_not_make_spf_failed(self):
        status, detected = _evaluate_txt(
            ["google-site-verification=abc123"],
            "include:matemail.online",
            "v=spf1",
        )
        self.assertEqual(status, DNSCheckStatus.MISSING)
        self.assertEqual(detected, "")

    def test_wrong_spf_is_reported_as_failed_spf(self):
        status, detected = _evaluate_txt(
            [
                "google-site-verification=abc123",
                "v=spf1 include:_spf.google.com ~all",
            ],
            "include:matemail.online",
            "v=spf1",
        )
        self.assertEqual(status, DNSCheckStatus.FAILED)
        self.assertEqual(detected, "v=spf1 include:_spf.google.com ~all")

    def test_correct_spf_wins_among_unrelated_txt_records(self):
        expected = "v=spf1 include:matemail.online ~all"
        status, detected = _evaluate_txt(
            ["google-site-verification=abc123", expected],
            "include:matemail.online",
            "v=spf1",
        )
        self.assertEqual(status, DNSCheckStatus.VERIFIED)
        self.assertEqual(detected, expected)
