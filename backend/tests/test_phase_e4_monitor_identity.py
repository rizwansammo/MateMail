"""Phase E4: the deployed monitor must check the new native mail identity.

Static deployment-contract tests catch a tempting but incorrect migration:
the product apex (matemail.pro) is not the mail-report-receiving domain.
The collector's MX probe must use mail.matemail.pro, whose MX is published.
"""
from pathlib import Path

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
