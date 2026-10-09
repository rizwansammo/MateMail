"""P4-C policy and safe installer contract tests; no network or production calls."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent


class MtastsContract(unittest.TestCase):
    def test_three_receiving_domains(self):
        source = (ROOT / "policy.txt").read_text()
        for domain in ("mail.matemail.pro", "netamate.com", "matedesk.pro"):
            result = subprocess.run(
                ["python3", str(ROOT / "validate.py"), "-", domain],
                input=source,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_never_publish_enforce_without_review(self):
        source = (ROOT / "policy.txt").read_text()
        self.assertIn("mode: testing", source)
        self.assertNotIn("mode: enforce", source)

    def test_reject_wrong_mx_and_enforcement(self):
        path = ROOT / "validate.py"
        original = (ROOT / "policy.txt").read_text()
        for corrupt in (original.replace("mx.matemail.pro", "attacker.example"),
                        original.replace("mode: testing", "mode: enforce"),
                        original + "mx: fallback.example\n"):
            result = subprocess.run(
                ["python3", str(path), "-", "mail.matemail.pro"],
                input=corrupt, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_whitelist_rejects_apex(self):
        result = subprocess.run(
            ["python3", str(ROOT / "validate.py"), str(ROOT / "policy.txt"), "matemail.pro"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_installer_syntax(self):
        subprocess.run(["bash", "-n", str(ROOT / "install.sh")], check=True)

    def test_installer_never_edits_dns_or_mail_engine(self):
        script = (ROOT / "install.sh").read_text()
        self.assertIn('DNS action required:', script)
        self.assertIn("certbot certonly --non-interactive --webroot", script)
        self.assertNotIn("systemctl restart postfix", script)
        self.assertNotIn("docker compose up", script)
        self.assertNotIn("mode: enforce", script)


if __name__ == "__main__":
    unittest.main()
