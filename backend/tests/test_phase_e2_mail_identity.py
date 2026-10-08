"""Phase E2: migration-specific contracts; no network or production credentials."""
from pathlib import Path
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class PhaseE2IdentityContractTest(SimpleTestCase):
    def _source(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_mail_hostname_and_sender_defaults_are_pro(self):
        base = self._source("backend/config/settings/base.py")
        prod = self._source("backend/config/settings/prod.py")
        self.assertIn('MAIL_HOSTNAME = env("MAIL_HOSTNAME", default="mx.matemail.pro")', base)
        self.assertIn('MAIL_DOMAIN = env("MAIL_DOMAIN", default="matemail.pro")', base)
        self.assertIn('default="MateMail <noreply@mail.matemail.pro>"', prod)
        self.assertIn('default=["noreply@mail.matemail.pro"]', base)

    def test_platform_sender_policy_passes_compose_boundary(self):
        compose = self._source("deploy/docker-compose.yml")
        self.assertIn("PLATFORM_SENDER_ADDRESSES:", compose)
        self.assertIn("DEFAULT_FROM_EMAIL:", compose)
        self.assertIn("noreply@mail.matemail.pro", compose)

    def test_postfix_identity_and_gateway_pro_alias_after_reset(self):
        postfix = self._source("deploy/native-engine/postfix/main.cf")
        gateway = self._source("deploy/native-engine/docker-compose.yml")
        self.assertIn("myhostname = mx.matemail.pro", postfix)
        self.assertIn("mydomain = matemail.pro", postfix)
        self.assertIn("          - mx.matemail.pro", gateway)
        self.assertNotIn("          - mx.matemail.online", gateway)

    def test_mail_tls_installer_uses_pro_only_certificate(self):
        installer = self._source("deploy/native-engine/scripts/install-mail-cert.sh")
        self.assertIn('HOST=', installer)
        self.assertIn("matemail-mail-pro", installer)
        self.assertNotIn("mx.matemail.online", installer)
        self.assertIn("mx.matemail.pro", installer)

    def test_cutover_instructions_require_dns_and_ptr_gate(self):
        guide = self._source("docs/PHASE_E2_NATIVE_IDENTITY_CUTOVER.md").lower()
        for required in ("mm1._domainkey.mail", "_dmarc.mail",
                         "include:_spf.matemail.pro", "169.58.114.252", "do not"):
            self.assertIn(required.lower(), guide)
