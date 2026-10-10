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

    def test_certbot_hook_is_scoped_to_our_own_managed_nginx_site(self):
        hook = (path.parent / "mta-sts-certbot-renew-hook.sh").read_text()
        installer = (path.parent / "install.sh").read_text()
        for gate in ("RENEWED_LINEAGE", "mta-sts.", "matemail-transport-sts-",
                     "# Managed by MateMail P4-C dynamic MTA-STS",
                     "readlink -f", "nginx -t", "systemctl reload nginx"):
            self.assertIn(gate, hook)
        self.assertIn("mta-sts-certbot-renew-hook.sh", installer)
        self.assertNotIn("certbot renew", installer)
        self.assertNotIn("systemctl enable", installer)

    def test_installer_never_enables_service(self):
        script = (path.parent / "install.sh").read_text()
        self.assertNotIn("systemctl enable", script)
        self.assertNotIn("systemctl start", script)
        self.assertIn("--install-only", script)



class RetirementContractTests(TestCase):
    def job(self):
        return {
            "id": "9755abf8-10de-4d5e-a6ab-6fc3e780cb35",
            "tenant_id": "7788abf8-10de-4d5e-a6ab-6fc3e780cb36",
            "domain": "customer.example",
            "hostname": "mta-sts.customer.example",
            "edge": "mta-sts-gateway.matemail.pro",
            "mx": "mx.matemail.pro",
            "policy_id": "a" * 24,
        }

    def test_none_policy_is_not_enforce_and_retains_cache_window(self):
        self.assertEqual(module.policy_none(),
                         b"version: STSv1\r\nmode: none\r\nmax_age: 86400\r\n")
        self.assertNotIn(b"mode: enforce", module.policy_none())

    def test_exact_dns_absence_refuses_servfail_and_remaining_txt(self):
        no_record = ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 100\n"
        no_domain = ";; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 101\n"
        present = (no_record +
                   '_mta-sts.customer.example. 3600 IN TXT "v=STSv1; id=x"\n')
        with mock.patch.object(module, "run", side_effect=[no_record, no_domain, no_record, no_domain]):
            self.assertTrue(module.public_txt_absent(self.job()))
        with mock.patch.object(module, "run", return_value=present):
            self.assertFalse(module.public_txt_absent(self.job()))
        with mock.patch.object(module, "run", return_value=";; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 1"):
            with self.assertRaises(module.EdgeError):
                module.public_txt_absent(self.job())
        with mock.patch.object(module, "run", return_value=""):
            with self.assertRaises(module.EdgeError):
                module.public_txt_absent(self.job())

    def test_refuse_cleanup_when_public_dns_reappears(self):
        data = self.job()
        with mock.patch.object(module, "retire_authorize"), mock.patch.object(
            module, "public_txt_absent", return_value=False,
        ), mock.patch.object(module, "retire_state") as state, mock.patch.object(
            module, "drop_managed_nginx",
        ) as cleanup:
            with self.assertRaises(module.EdgeError):
                module.retire_cleanup(data)
        state.assert_called_once_with(data, "dns-check", absent=False)
        cleanup.assert_not_called()

    def test_unmanaged_nginx_site_is_never_retired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "matemail-transport-sts-mta-sts.customer.example.conf").write_text("# operator-owned\n")
            with mock.patch.object(module, "SITES", root), mock.patch.object(module, "ENABLED", root):
                with self.assertRaises(module.EdgeError):
                    module.managed_site(self.job()["hostname"])

    def test_cleanup_deletes_only_owned_resources_and_keeps_other_sites(self):
        data = self.job()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site_dir = root / "sites"
            enabled = root / "enabled"
            policies = root / "policies"
            journal = root / "journal"
            for d in (site_dir, enabled, policies):
                d.mkdir()
            host = data["hostname"]
            site = site_dir / ("matemail-transport-sts-" + host + ".conf")
            site.write_text(module.https_site(host))
            (enabled / site.name).symlink_to(site)
            other = enabled / "unrelated-service.conf"
            other.write_text("server { server_name unrelated.example; }\n")
            policy_file = policies / host / ".well-known" / "mta-sts.txt"
            policy_file.parent.mkdir(parents=True)
            policy_file.write_bytes(module.policy_none())
            with mock.patch.object(module, "SITES", site_dir), mock.patch.object(
                module, "ENABLED", enabled,
            ), mock.patch.object(module, "POLICIES", policies), mock.patch.object(
                module, "RECEIPTS", journal,
            ), mock.patch.object(module, "retire_authorize"), mock.patch.object(
                module, "retire_state",
            ) as state, mock.patch.object(module, "public_txt_absent", return_value=True), mock.patch.object(
                module, "delete_dedicated_certificate",
            ) as certbot, mock.patch.object(module, "run", return_value=""):
                module.retire_cleanup(data)
            self.assertFalse(site.exists())
            self.assertFalse((enabled / site.name).exists())
            self.assertFalse(policy_file.exists())
            self.assertFalse(journal.joinpath(host + ".json").exists())
            self.assertTrue(other.is_file())
            certbot.assert_called_once_with(host)
            state.assert_called_once_with(data, "complete")

    def test_nginx_validation_failure_restores_owned_site(self):
        data = self.job()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sites = root / "sites"
            enabled = root / "enabled"
            receipts = root / "receipts"
            sites.mkdir()
            enabled.mkdir()
            host = data["hostname"]
            path = sites / ("matemail-transport-sts-" + host + ".conf")
            path.write_text(module.https_site(host))
            link = enabled / path.name
            link.symlink_to(path)
            with mock.patch.object(module, "SITES", sites), mock.patch.object(
                module, "ENABLED", enabled,
            ), mock.patch.object(module, "RECEIPTS", receipts), mock.patch.object(
                module, "run",
                side_effect=[module.EdgeError("nginx-test-failed"), "", ""],
            ):
                with self.assertRaises(module.EdgeError):
                    module.drop_managed_nginx(data)
            self.assertTrue(path.is_file())
            self.assertTrue(link.is_symlink())

    def test_no_edged_host_may_retire_only_when_dns_is_absent(self):
        data = self.job()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sites = root / "sites"
            enabled = root / "enabled"
            policy = root / "policies"
            for path in (sites, enabled, policy):
                path.mkdir()
            with mock.patch.object(module, "SITES", sites), mock.patch.object(
                module, "ENABLED", enabled,
            ), mock.patch.object(module, "POLICIES", policy), mock.patch.object(
                module, "retire_authorize",
            ), mock.patch.object(module, "public_txt_absent", return_value=True), mock.patch.object(
                module, "retire_state",
            ) as state:
                module.retire_to_none(data)
            state.assert_called_once_with(data, "mode-none")


    def test_certificate_retirement_rejects_shared_san_and_other_nginx_references(self):
        host = self.job()["hostname"]
        with mock.patch.object(module.Path, "exists", return_value=True), mock.patch.object(
            module, "run", return_value="Certificate Name: " + host + "\n    Domains: other.example\n",
        ):
            with self.assertRaises(module.EdgeError):
                module.delete_dedicated_certificate(host)
        with mock.patch.object(module.Path, "exists", return_value=True), mock.patch.object(
            module, "run", side_effect=[
                "Certificate Name: " + host + "\n    Domains: " + host + "\n",
                "server { ssl_certificate /etc/letsencrypt/live/" + host + "/fullchain.pem; }\n",
            ],
        ):
            with self.assertRaises(module.EdgeError):
                module.delete_dedicated_certificate(host)

    def test_partial_vhost_without_cert_needs_both_dns_records_removed(self):
        data = self.job()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sites, enabled, policies = root / "sites", root / "enabled", root / "policies"
            for folder in (sites, enabled, policies):
                folder.mkdir()
            host = data["hostname"]
            vhost = sites / ("matemail-transport-sts-" + host + ".conf")
            vhost.write_text(module.bootstrap(host))
            (enabled / vhost.name).symlink_to(vhost)
            source = policies / host / ".well-known" / "mta-sts.txt"
            source.parent.mkdir(parents=True)
            source.write_bytes(module.policy("mx.matemail.pro"))
            with mock.patch.object(module, "SITES", sites), mock.patch.object(
                module, "ENABLED", enabled,
            ), mock.patch.object(module, "POLICIES", policies), mock.patch.object(
                module, "retire_authorize",
            ), mock.patch.object(module, "cert_ok", return_value=False), mock.patch.object(
                module, "public_txt_absent", return_value=False,
            ), mock.patch.object(module, "retire_state") as state:
                with self.assertRaises(module.EdgeError):
                    module.retire_to_none(data)
            state.assert_not_called()
            self.assertEqual(source.read_bytes(), module.policy("mx.matemail.pro"))
            with mock.patch.object(module, "SITES", sites), mock.patch.object(
                module, "ENABLED", enabled,
            ), mock.patch.object(module, "POLICIES", policies), mock.patch.object(
                module, "retire_authorize",
            ), mock.patch.object(module, "cert_ok", return_value=False), mock.patch.object(
                module, "public_txt_absent", return_value=True,
            ), mock.patch.object(module, "retire_state") as state:
                module.retire_to_none(data)
            self.assertEqual(source.read_bytes(), module.policy_none())
            state.assert_called_once_with(data, "mode-none")



    def test_resume_after_crash_between_nginx_unlink_and_source_removal(self):
        data = self.job()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sites, enabled, journal = root / "sites", root / "enabled", root / "receipts"
            for folder in (sites, enabled):
                folder.mkdir()
            host = data["hostname"]
            site = sites / ("matemail-transport-sts-" + host + ".conf")
            site.write_text(module.https_site(host))
            link = enabled / site.name
            link.symlink_to(site)
            other = enabled / "unrelated.conf"
            other.write_text("server { server_name unrelated.example; }\n")
            with mock.patch.object(module, "SITES", sites), mock.patch.object(
                module, "ENABLED", enabled,
            ), mock.patch.object(module, "RECEIPTS", journal), mock.patch.object(
                module, "run", side_effect=[SystemExit("crash during nginx reload")],
            ):
                with self.assertRaises(SystemExit):
                    module.drop_managed_nginx(data)
            self.assertFalse(link.is_symlink())
            self.assertTrue(site.exists())
            self.assertTrue((journal / (host + ".json")).exists())
            # A subsequent worker invocation must complete without deleting an
            # unrelated site or treating the interrupted removal as unmanaged.
            with mock.patch.object(module, "SITES", sites), mock.patch.object(
                module, "ENABLED", enabled,
            ), mock.patch.object(module, "RECEIPTS", journal), mock.patch.object(
                module, "run", return_value="",
            ):
                receipt = module.drop_managed_nginx(data)
                self.assertEqual(receipt, journal / (host + ".json"))
            self.assertFalse(site.exists())
            self.assertTrue(other.exists())

    def test_interrupted_cleanup_refuses_unmanaged_replacement(self):
        data = self.job()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sites, enabled, journal = root / "sites", root / "enabled", root / "receipts"
            for folder in (sites, enabled):
                folder.mkdir()
            host = data["hostname"]
            path = sites / ("matemail-transport-sts-" + host + ".conf")
            path.write_text(module.https_site(host))
            link = enabled / path.name
            link.symlink_to(path)
            with mock.patch.object(module, "SITES", sites), mock.patch.object(
                module, "ENABLED", enabled,
            ), mock.patch.object(module, "RECEIPTS", journal), mock.patch.object(
                module, "run", side_effect=[SystemExit("crash")],
            ):
                with self.assertRaises(SystemExit):
                    module.drop_managed_nginx(data)
            path.write_text("# unrelated operator-managed\nserver { server_name unrelated.example; }\n")
            with mock.patch.object(module, "SITES", sites), mock.patch.object(
                module, "ENABLED", enabled,
            ), mock.patch.object(module, "RECEIPTS", journal), mock.patch.object(
                module, "run", return_value="",
            ):
                with self.assertRaises(module.EdgeError):
                    module.drop_managed_nginx(data)
            self.assertTrue(path.exists())
