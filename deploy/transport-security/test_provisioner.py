"""P4-C.C standalone privileged-edge pure contract tests (no network/root)."""
import importlib.util
from pathlib import Path
import tempfile
from unittest import TestCase, mock

path = Path(__file__).resolve().parent / "provisioner.py"
spec = importlib.util.spec_from_file_location("transport_sts_provisioner", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EdgeContractTests(TestCase):
    def make_job(self):
        return {
            "id": "9755abf8-10de-4d5e-a6ab-6fc3e780cb35",
            "tenant_id": "7788abf8-10de-4d5e-a6ab-6fc3e780cb36",
            "domain": "customer.example",
            "hostname": "mta-sts.customer.example",
            "edge": "mta-sts-gateway.matemail.pro",
            "mx": "mx.matemail.pro",
            "policy_id": "c" * 24,
        }

    def test_policy_has_crlf_and_testing_only(self):
        self.assertEqual(
            module.policy("mx.matemail.pro"),
            b"version: STSv1\r\nmode: testing\r\nmx: mx.matemail.pro\r\nmax_age: 86400\r\n"
        )

    def test_jobs_are_scoped_to_exact_derived_hostname(self):
        self.assertEqual(module.job(self.make_job())["hostname"], "mta-sts.customer.example")
        for key, value in (
            ("hostname", "mta-sts.other-domain.example"),
            ("hostname", "bad;touch /tmp/file"),
            ("policy_id", "enforce"),
            ("id", "not-uuid"),
        ):
            incoming = {**self.make_job(), key: value}
            with self.subTest(key=key), self.assertRaises(module.EdgeError):
                module.job(incoming)

    def test_nginx_exact_policy_path_no_proxy(self):
        vhost = module.https_site("mta-sts.customer.example")
        self.assertIn("location = /.well-known/mta-sts.txt", vhost)
        self.assertIn("location / { return 404; }", vhost)
        self.assertNotIn("proxy_pass", vhost)
        self.assertNotIn("ssl_verify off", vhost)

    def test_never_overwrite_unmanaged_nginx_site(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(module, "SITES", Path(tmp)), mock.patch.object(
            module, "ENABLED", Path(tmp)
        ):
            host = "mta-sts.customer.example"
            module.site_file(host).write_text("# Other operator\n")
            with self.assertRaises(module.EdgeError):
                module.collision_check(host)

    def test_reject_existing_legacy_mta_sts_vhost(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "legacy").write_text("server_name mta-sts.customer.example;")
            with mock.patch.object(module, "SITES", root), mock.patch.object(module, "ENABLED", root):
                with self.assertRaises(module.EdgeError):
                    module.collision_check("mta-sts.customer.example")

    def test_independent_dns_checks_before_issuance(self):
        fake = self.make_job()
        with mock.patch.object(module, "PUBLIC_IP", "169.58.114.252"), mock.patch.object(
            module, "dns", side_effect=[
                ["mta-sts-gateway.matemail.pro."],
                ["10 mx.matemail.pro."],
                ["169.58.114.252"],
            ]
        ):
            module.check_dns(fake)

    def test_wrong_mx_and_wrong_edge_refused(self):
        with mock.patch.object(module, "PUBLIC_IP", "169.58.114.252"):
            for responses in (
                [["wrong.example."], ["10 mx.matemail.pro."], ["169.58.114.252"]],
                [["mta-sts-gateway.matemail.pro."], ["10 attacker.example."], ["169.58.114.252"]],
                [["mta-sts-gateway.matemail.pro."], ["10 mx.matemail.pro."], ["203.0.113.10"]],
            ):
                with mock.patch.object(module, "dns", side_effect=responses):
                    with self.assertRaises(module.EdgeError):
                        module.check_dns(self.make_job())

    def test_public_policy_path_traversable_despite_strict_umask(self):
        # The real systemd unit uses UMask=0077, so mkdir creates 0700.
        # Nginx must reach the public policy file without directory listing.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "transport-sts"
            root.mkdir(mode=0o700)
            host = "mta-sts.customer.example"
            policy_dir = root / host / ".well-known"
            policy_dir.mkdir(parents=True, mode=0o700)
            (root / host).chmod(0o700)
            policy_file = policy_dir / "mta-sts.txt"
            policy_file.write_bytes(module.policy("mx.matemail.pro"))
            policy_file.chmod(0o644)
            with mock.patch.object(module, "POLICIES", root):
                module.expose_public_policy_path(host)
            for directory in (root, root / host, policy_dir):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o711)
            self.assertEqual(policy_file.stat().st_mode & 0o777, 0o644)

    def test_public_policy_path_rejects_directory_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "transport-sts"
            root.mkdir()
            (root / "mta-sts.customer.example").symlink_to(Path(temp))
            with mock.patch.object(module, "POLICIES", root):
                with self.assertRaises(module.EdgeError):
                    module.expose_public_policy_path("mta-sts.customer.example")

    def test_installer_never_enables_service(self):
        script = (path.parent / "install.sh").read_text()
        self.assertNotIn("systemctl enable", script)
        self.assertNotIn("systemctl start", script)
        self.assertIn("--install-only", script)
