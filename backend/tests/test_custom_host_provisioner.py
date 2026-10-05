"""
Custom-host edge worker contracts (Phase 3 TLS + Phase 4 activation).

These tests never call nginx, Certbot or systemd. They exercise the generated
configuration and the privilege-boundary behavior with temporary directories
and stubs, so CI can prove the host automation without needing root.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER_PATH = ROOT / "deploy" / "custom-hosts" / "provisioner.py"
HOOK_PATH = ROOT / "deploy" / "custom-hosts" / "renew-hook.sh"
INSTALLER_PATH = ROOT / "deploy" / "custom-hosts" / "install.sh"
SMOKE_PATH = ROOT / "deploy" / "custom-hosts" / "smoke_test.py"
SERVICE_PATH = (
    ROOT
    / "deploy"
    / "custom-hosts"
    / "systemd"
    / "matemail-custom-host-provisioner.service"
)
DEPLOY_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "deploy.yml"

spec = importlib.util.spec_from_file_location("matemail_custom_host_provisioner", WORKER_PATH)
worker = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(worker)


class WorkerValidationTest(unittest.TestCase):
    def test_hostnames_are_strict_data_not_shell(self):
        for value in (
            "mail.example.com;rm -rf /",
            "$(id).example.com",
            "mail.example.com/path",
            "*.example.com",
            "matemail.online",
            "custom.matemail.online",
        ):
            with self.subTest(value=value):
                with self.assertRaises(worker.ProvisioningError):
                    worker.validate_hostname(value)

        self.assertEqual(
            worker.validate_hostname("MAIL.Customer-Example.COM."),
            "mail.customer-example.com",
        )

    def test_job_requires_uuid_surface_and_tenant(self):
        good = worker.validate_job(
            {
                "id": "0a410cf7-b655-466e-929f-727ed6444309",
                "hostname": "mail.customer.com",
                "surface": "postbox",
                "tenant_id": "0e40081a-b654-4901-afec-d6cb741acdf6",
            }
        )
        self.assertEqual(good["surface"], "postbox")

        bad = dict(good, surface="platform")
        with self.assertRaises(worker.ProvisioningError):
            worker.validate_job(bad)


class FrontendOverrideTest(unittest.TestCase):
    def test_branded_frontend_override_is_loopback_only(self):
        parsed = worker.frontend_overrides(
            "mailadmin.netamate.com=http://127.0.0.1:3060,"
            "postbox.netamate.com=http://127.0.0.1:3060"
        )
        self.assertEqual(
            parsed["mailadmin.netamate.com"],
            "http://127.0.0.1:3060",
        )

        for unsafe in (
            "mail.customer.com=http://example.com:3000",
            "mail.customer.com=http://10.0.0.5:3000",
            "mail.customer.com=http://127.0.0.1:80",
            "mail.customer.com=http://127.0.0.1:70000",
            "mail.customer.com=http://127.0.0.1:3060;include /tmp/x",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(worker.ProvisioningError):
                    worker.frontend_overrides(unsafe)

    def test_active_vhost_uses_branded_override_only_for_exact_host(self):
        original = worker.FRONTEND_OVERRIDES_RAW
        try:
            worker.FRONTEND_OVERRIDES_RAW = (
                "mailadmin.netamate.com=http://127.0.0.1:3060"
            )
            branded = worker.active_vhost("mailadmin.netamate.com", "hub")
            ordinary = worker.active_vhost("manage.customer.com", "hub")
        finally:
            worker.FRONTEND_OVERRIDES_RAW = original

        self.assertIn("proxy_pass             http://127.0.0.1:3060;", branded)
        self.assertIn("proxy_pass             http://matemail_frontend;", ordinary)


class GeneratedNginxTest(unittest.TestCase):
    def test_bootstrap_is_acme_only(self):
        text = worker.bootstrap_vhost("mail.customer.com")
        self.assertTrue(text.startswith(worker.GENERATED_MARKER))
        self.assertIn("server_name mail.customer.com;", text)
        self.assertIn("/.well-known/acme-challenge/", text)
        self.assertNotIn("listen 443", text)
        self.assertNotIn("proxy_pass", text)
        self.assertIn('return 503 "MateMail custom domain is being provisioned.', text)

    def test_ready_vhost_terminates_tls_but_does_not_route_phase_4_traffic(self):
        text = worker.ready_vhost("mail.customer.com", "postbox")
        self.assertIn("listen 443 ssl;", text)
        self.assertIn(
            "/etc/letsencrypt/live/mail.customer.com/fullchain.pem",
            text,
        )
        self.assertIn("surface=postbox", text)
        self.assertNotIn("proxy_pass", text)
        self.assertIn('return 503 "MateMail custom domain is ready', text)


    def test_active_hub_vhost_routes_same_origin_and_blocks_other_surfaces(self):
        text = worker.active_vhost("manage.customer.com", "hub")
        self.assertIn("server_name manage.customer.com;", text)
        self.assertIn("proxy_pass             http://matemail_backend;", text)
        self.assertIn("proxy_pass             http://matemail_frontend;", text)
        self.assertIn("X-MateMail-Custom-Host   1", text)
        self.assertIn("X-MateMail-Surface       hub", text)
        self.assertIn("location ^~ /api/postbox/ { return 404; }", text)
        self.assertIn("location ^~ /api/platform/ { return 404; }", text)
        self.assertIn("location ^~ /platform { return 404; }", text)
        self.assertIn("location ^~ /signup { return 404; }", text)
        self.assertIn("location = /api/auth/signup/ { return 404; }", text)
        self.assertNotIn("portal.matemail.online", text)
        self.assertNotIn("postbox.matemail.online", text)

    def test_active_postbox_vhost_exposes_only_postbox_api(self):
        text = worker.active_vhost("inbox.customer.com", "postbox")
        self.assertIn("X-MateMail-Surface       postbox", text)
        self.assertIn("location ^~ /api/postbox/", text)
        self.assertIn("location ^~ /api/ { return 404; }", text)
        self.assertIn("proxy_buffering        off;", text)
        self.assertNotIn("portal.matemail.online", text)
        self.assertNotIn("return 301 https://postbox.matemail.online", text)

    def test_candidate_failure_restores_previous_generated_site(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            available = base / "available"
            enabled = base / "enabled"
            available.mkdir()
            enabled.mkdir()

            old_available = worker.SITES_AVAILABLE
            old_enabled = worker.SITES_ENABLED
            old_run = worker.run
            try:
                worker.SITES_AVAILABLE = available
                worker.SITES_ENABLED = enabled
                host = "mail.customer.com"
                path = worker.site_path(host)
                link = worker.enabled_path(host)
                previous = (
                    worker.GENERATED_MARKER
                    + "\nserver { listen 80; server_name mail.customer.com; }\n"
                )
                path.write_text(previous, encoding="utf-8")
                link.symlink_to(path)

                def fail_nginx(argv, *, capture=True):
                    if argv[-1] == "-t":
                        raise worker.ProvisioningError("nginx failed")
                    raise AssertionError("reload must not run after failed nginx -t")

                worker.run = fail_nginx

                with self.assertRaises(worker.ProvisioningError):
                    worker.install_site(
                        host,
                        worker.bootstrap_vhost(host),
                    )

                self.assertEqual(path.read_text(encoding="utf-8"), previous)
                self.assertTrue(link.is_symlink())
            finally:
                worker.SITES_AVAILABLE = old_available
                worker.SITES_ENABLED = old_enabled
                worker.run = old_run

    def test_generated_site_can_be_removed_but_operator_site_cannot(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            available = base / "available"
            enabled = base / "enabled"
            available.mkdir()
            enabled.mkdir()

            old_available = worker.SITES_AVAILABLE
            old_enabled = worker.SITES_ENABLED
            old_run = worker.run
            try:
                worker.SITES_AVAILABLE = available
                worker.SITES_ENABLED = enabled
                host = "mail.customer.com"
                path = worker.site_path(host)
                link = worker.enabled_path(host)
                path.write_text(
                    worker.GENERATED_MARKER
                    + "\nserver { listen 80; server_name mail.customer.com; }\n",
                    encoding="utf-8",
                )
                link.symlink_to(path)
                calls = []
                worker.run = lambda argv, **kw: calls.append(tuple(argv))

                worker.remove_generated_site(host)

                self.assertFalse(path.exists())
                self.assertFalse(link.exists())
                self.assertIn((worker.NGINX, "-t"), calls)

                path.write_text("# operator-owned\n", encoding="utf-8")
                with self.assertRaises(worker.ProvisioningError):
                    worker.remove_generated_site(host)
                self.assertEqual(path.read_text(encoding="utf-8"), "# operator-owned\n")
            finally:
                worker.SITES_AVAILABLE = old_available
                worker.SITES_ENABLED = old_enabled
                worker.run = old_run

    def test_non_generated_nginx_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            available = base / "available"
            enabled = base / "enabled"
            available.mkdir()
            enabled.mkdir()

            old_available = worker.SITES_AVAILABLE
            old_enabled = worker.SITES_ENABLED
            try:
                worker.SITES_AVAILABLE = available
                worker.SITES_ENABLED = enabled
                host = "mail.customer.com"
                path = worker.site_path(host)
                path.write_text("# operator-owned\n", encoding="utf-8")

                with self.assertRaises(worker.ProvisioningError):
                    worker.install_site(host, worker.bootstrap_vhost(host))

                self.assertEqual(path.read_text(encoding="utf-8"), "# operator-owned\n")
            finally:
                worker.SITES_AVAILABLE = old_available
                worker.SITES_ENABLED = old_enabled


class ProvisioningFlowTest(unittest.TestCase):
    def test_worker_can_only_finish_at_ready(self):
        calls = []
        job = {
            "id": "0a410cf7-b655-466e-929f-727ed6444309",
            "hostname": "mail.customer.com",
            "surface": "postbox",
            "tenant_id": "0e40081a-b654-4901-afec-d6cb741acdf6",
        }

        originals = {
            "authorize": worker.authorize,
            "post_state": worker.post_state,
            "install_site": worker.install_site,
            "issue_certificate": worker.issue_certificate,
            "verify_certificate": worker.verify_certificate,
        }
        try:
            worker.authorize = lambda value: calls.append(("authorize", value["hostname"]))
            worker.post_state = lambda job_id, state, **kw: calls.append(
                ("state", state, kw.get("certificate_status"))
            )
            worker.install_site = lambda hostname, content: calls.append(
                ("site", "443" if "listen 443 ssl" in content else "80")
            )
            worker.issue_certificate = lambda hostname: calls.append(("certbot", hostname))
            worker.verify_certificate = lambda hostname: calls.append(("certcheck", hostname))

            worker.provision(job)
        finally:
            for name, value in originals.items():
                setattr(worker, name, value)

        states = [item for item in calls if item[0] == "state"]
        self.assertEqual(
            states,
            [
                ("state", "provisioning", "issuing"),
                ("state", "ready", "active"),
            ],
        )
        self.assertNotIn(("state", "active", "active"), states)
        self.assertEqual(sum(1 for item in calls if item[0] == "authorize"), 2)




class ActivationFlowTest(unittest.TestCase):
    def test_activation_installs_route_before_marking_database_active(self):
        calls = []
        job = {
            "id": "0a410cf7-b655-466e-929f-727ed6444309",
            "hostname": "inbox.customer.com",
            "surface": "postbox",
            "tenant_id": "0e40081a-b654-4901-afec-d6cb741acdf6",
        }

        originals = {
            "authorize": worker.authorize,
            "post_state": worker.post_state,
            "install_site": worker.install_site,
            "verify_certificate": worker.verify_certificate,
        }
        try:
            worker.authorize = lambda value: calls.append(("authorize", value["hostname"]))
            worker.verify_certificate = lambda hostname: calls.append(("certcheck", hostname))
            worker.install_site = lambda hostname, content: calls.append(
                ("site", "postbox" if "X-MateMail-Surface       postbox" in content else "other")
            )
            worker.post_state = lambda job_id, state, **kw: calls.append(
                ("state", state, kw.get("certificate_status"))
            )

            worker.activate(job)
        finally:
            for name, value in originals.items():
                setattr(worker, name, value)

        site_index = calls.index(("site", "postbox"))
        state_index = calls.index(("state", "active", "active"))
        self.assertLess(site_index, state_index)
        self.assertEqual(sum(1 for item in calls if item[0] == "authorize"), 2)


class DeactivationFlowTest(unittest.TestCase):
    def test_deactivation_removes_edge_before_marking_database_inactive(self):
        calls = []
        job = {
            "id": "0a410cf7-b655-466e-929f-727ed6444309",
            "hostname": "inbox.customer.com",
            "surface": "postbox",
            "tenant_id": "0e40081a-b654-4901-afec-d6cb741acdf6",
        }

        originals = {
            "remove_generated_site": worker.remove_generated_site,
            "retire_certificate": worker.retire_certificate,
            "post_state": worker.post_state,
        }
        try:
            worker.remove_generated_site = lambda hostname: calls.append(
                ("site-remove", hostname)
            )
            worker.retire_certificate = lambda hostname: calls.append(
                ("cert-retire", hostname)
            )
            worker.post_state = lambda job_id, state, **kw: calls.append(
                ("state", state, kw.get("certificate_status"))
            )

            worker.deactivate(job)
        finally:
            for name, value in originals.items():
                setattr(worker, name, value)

        self.assertLess(
            calls.index(("site-remove", "inbox.customer.com")),
            calls.index(("cert-retire", "inbox.customer.com")),
        )
        self.assertLess(
            calls.index(("cert-retire", "inbox.customer.com")),
            calls.index(("state", "inactive", "revoked")),
        )

    def test_certificate_retirement_uses_certbot_not_manual_deletion(self):
        old_run = worker.run
        old_exists = Path.exists
        calls = []
        try:
            # Patch Path.exists narrowly for the expected Certbot live cert.
            def fake_exists(path):
                if str(path).endswith(
                    "/etc/letsencrypt/live/inbox.customer.com/cert.pem"
                ):
                    return True
                return old_exists(path)

            Path.exists = fake_exists
            worker.run = lambda argv, **kw: calls.append(tuple(argv))
            worker.retire_certificate("inbox.customer.com")
        finally:
            Path.exists = old_exists
            worker.run = old_run

        command = calls[0]
        self.assertEqual(command[0], worker.CERTBOT)
        self.assertIn("revoke", command)
        self.assertIn("--delete-after-revoke", command)
        self.assertIn("cessationofoperation", command)


class HostInstallArtifactsTest(unittest.TestCase):
    def test_renew_hook_is_scoped_to_generated_custom_sites(self):
        source = HOOK_PATH.read_text(encoding="utf-8")
        self.assertIn('RENEWED_LINEAGE', source)
        self.assertIn("matemail-custom-", source)
        self.assertIn(worker.GENERATED_MARKER, source)
        self.assertIn("/usr/sbin/nginx -t", source)
        self.assertIn("/bin/systemctl reload nginx", source)

    def test_systemd_worker_is_root_but_filesystem_scoped(self):
        source = SERVICE_PATH.read_text(encoding="utf-8")
        self.assertIn("User=root", source)
        self.assertIn("ProtectSystem=strict", source)
        self.assertIn("NoNewPrivileges=true", source)
        self.assertIn(
            "ReadWritePaths=/etc/nginx /etc/letsencrypt /var/lib/letsencrypt",
            source,
        )

    def test_installer_never_prints_the_shared_secret(self):
        source = INSTALLER_PATH.read_text(encoding="utf-8")
        self.assertNotIn('echo "$SECRET"', source)
        self.assertNotIn('printf "%s" "$SECRET"', source)
        self.assertIn("chmod 0600", source)
        self.assertIn("--activate", source)
        self.assertIn("MATEMAIL_CUSTOM_HOST_FRONTEND_OVERRIDES", source)

    def test_production_smoke_tool_is_valid_python_and_read_only_by_contract(self):
        source = SMOKE_PATH.read_text(encoding="utf-8")
        compile(source, str(SMOKE_PATH), "exec")
        self.assertIn("http.client.HTTPSConnection", source)
        self.assertIn("dig", source)
        self.assertNotIn("certbot", source.lower())
        self.assertNotIn("systemctl", source.lower())
        self.assertNotIn("docker", source.lower())

    def test_manual_deploy_stages_worker_from_the_exact_release_sha(self):
        source = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn('git archive "${IMAGE_TAG}" deploy/custom-hosts', source)
        self.assertIn('custom-hosts.${IMAGE_TAG}.tar.gz', source)
        self.assertIn('bash "$CUSTOM_HOST_DIR/install.sh"', source)
        self.assertIn(
            "systemctl enable --now matemail-custom-host-provisioner.timer",
            source,
        )
        # A customer ACME failure is isolated from the healthy application
        # release; it must not trigger the Compose rollback path.
        timer = source.index(
            "systemctl enable --now matemail-custom-host-provisioner.timer"
        )
        disarm = source.index("ROLLBACK_ARMED=0")
        self.assertGreater(timer, disarm)


if __name__ == "__main__":
    unittest.main()
