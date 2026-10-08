"""Phase E4: the deployed monitor must check the new native mail identity.

Static deployment-contract tests catch a tempting but incorrect migration:
the product apex (matemail.pro) is not the mail-report-receiving domain.
The collector's MX probe must use mail.matemail.pro, whose MX is published.
"""
from pathlib import Path
from types import SimpleNamespace

from django.test import override_settings

from apps.dnshealth.services import _expected_records

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_e4_monitor_collector_uses_real_sender_identity_and_dual_certificate():
    src = (PROJECT_ROOT / "deploy/monitoring/collectors/matemail_collector.py").read_text()
    assert 'MAIL_HOSTNAME = os.environ.get("MAIL_HOSTNAME", "mx.matemail.pro")' in src
    assert 'MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "matemail.pro")' in src
    assert 'SENDER_DOMAIN = os.environ.get("SENDER_DOMAIN", "mail.matemail.pro")' in src
    assert 'MX_CHECK_DOMAIN = os.environ.get("MX_CHECK_DOMAIN", SENDER_DOMAIN)' in src
    assert 'mx = _dig("MX", MX_CHECK_DOMAIN)' in src
    assert 'MAIL_CERT_NAME = os.environ.get("MAIL_CERT_NAME", "matemail-mail-dual")' in src
    assert '"/etc/letsencrypt/live/%s/fullchain.pem" % MAIL_CERT_NAME' in src
    assert "mx.matemail.online" not in src


def test_e4_monitor_installer_deploys_canonical_dns_expectations():
    script = (PROJECT_ROOT / "deploy/monitoring/install.sh").read_text()
    for expected in (
        "MAIL_HOSTNAME=mx.matemail.pro",
        "MAIL_DOMAIN=matemail.pro",
        "SENDER_DOMAIN=mail.matemail.pro",
        "MX_CHECK_DOMAIN=mail.matemail.pro",
        "MAIL_CERT_NAME=matemail-mail-dual",
    ):
        assert expected in script
    assert "MAIL_HOSTNAME=mx.matemail.online" not in script


@override_settings(DMARC_AGGREGATE_REPORTING_ENABLED=False)
def test_e4_onboarding_is_dynamic_without_external_reporting_authorization():
    for domain_name in ("customer-a.example", "customer-b.example", "future-c.example"):
        domain = SimpleNamespace(domain=domain_name, dkim_selector="mm1", dkim_public_key="PUBLIC")
        records = _expected_records(domain)
        dmarc = next(r for r in records if r["label"] == "DMARC")
        assert dmarc["host"] == f"_dmarc.{domain_name}"
        assert dmarc["expected_value"] == "v=DMARC1; p=none"
        assert not any("_report._dmarc." in r["host"] for r in records)


@override_settings(
    DMARC_AGGREGATE_REPORTING_ENABLED=True,
    DMARC_REPORT_ADDRESS="dmarc@mail.matemail.pro",
)
def test_e4_optional_central_reporting_uses_one_configured_mailbox_for_any_domain():
    for domain_name in ("customer-a.example", "customer-b.example"):
        domain = SimpleNamespace(domain=domain_name, dkim_selector="mm1", dkim_public_key="PUBLIC")
        records = _expected_records(domain)
        dmarc = next(r for r in records if r["label"] == "DMARC")
        assert dmarc["host"] == f"_dmarc.{domain_name}"
        assert dmarc["expected_value"] == (
            "v=DMARC1; p=none; rua=mailto:dmarc@mail.matemail.pro"
        )


def test_e4_dmarc_report_address_passes_through_compose():
    base = (PROJECT_ROOT / "backend/config/settings/base.py").read_text()
    compose = (PROJECT_ROOT / "deploy/docker-compose.yml").read_text()
    env = (PROJECT_ROOT / "deploy/env.production.example").read_text()
    assert 'DMARC_REPORT_ADDRESS = env("DMARC_REPORT_ADDRESS", default="dmarc@mail.matemail.pro")' in base
    assert 'DMARC_REPORT_ADDRESS: ${DMARC_REPORT_ADDRESS:-dmarc@mail.matemail.pro}' in compose
    assert "DMARC_REPORT_ADDRESS=dmarc@mail.matemail.pro" in env
