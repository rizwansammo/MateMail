"""
The versioned private engine gateway configuration.

`deploy/engine/` holds the only part of the Mail Engine's Compose project that
MateMail owns, and it is the entire mechanism by which MateMail reaches the
engine. It is versioned so the path is recoverable without the server.

These tests pin the properties that carry security weight — the ones whose loss
would be silent. A gateway that publishes a host port, terminates TLS, or joins
an extra network still works; it just quietly undoes the reason it exists.
"""
import pathlib
import re

import yaml
from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
ENGINE_DIR = REPO / "deploy" / "engine"
OVERRIDE = ENGINE_DIR / "docker-compose.override.yml"
HAPROXY = ENGINE_DIR / "haproxy.cfg"
README = ENGINE_DIR / "README.md"

SERVICE = "matemail-engine-gateway"
LINK = "matemail_engine_link"
ENGINE_NET = "mailcow-network"
ENGINE_HOST = "mx.matemail.online"
GATEWAY_IP = "10.244.0.247"


class GatewayAssetsExistTest(SimpleTestCase):
    def test_the_compose_override_is_versioned(self):
        self.assertTrue(OVERRIDE.is_file(), f"{OVERRIDE} is missing")

    def test_the_haproxy_config_is_versioned(self):
        self.assertTrue(HAPROXY.is_file(), f"{HAPROXY} is missing")

    def test_a_readme_explains_the_deployment(self):
        self.assertTrue(README.is_file(), f"{README} is missing")

    def test_both_files_use_unix_line_endings(self):
        """
        They are copied onto a Linux host. A CRLF haproxy.cfg is a startup
        failure, and .gitattributes pins these extensions to eol=lf so a
        Windows checkout cannot introduce them.
        """
        for path in (OVERRIDE, HAPROXY):
            self.assertNotIn(b"\r\n", path.read_bytes(), f"{path.name} has CRLF")


class NoSecretsInVersionedEngineAssetsTest(SimpleTestCase):
    """
    Everything here is copied verbatim from a production host. A credential
    reaching the repository this way would be committed, pushed and mirrored
    before anyone noticed.
    """

    #: Assignment of a credential-shaped value, not a mention of the concept.
    #: The files are heavily commented and explaining *why* the API key matters
    #: is exactly what they should do.
    ASSIGNMENTS = re.compile(
        r"(password|passwd|secret|token|api[_-]?key|credential)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9+/=_.-]{8,}",
        re.IGNORECASE,
    )

    def test_no_credential_assignment_in_any_versioned_engine_file(self):
        for path in sorted(ENGINE_DIR.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            hit = self.ASSIGNMENTS.search(text)
            self.assertIsNone(
                hit, f"{path.name} looks like it assigns a credential: {hit.group(0) if hit else ''!r}"
            )

    def test_no_private_key_material(self):
        for path in sorted(ENGINE_DIR.rglob("*")):
            if not path.is_file():
                continue
            blob = path.read_text(encoding="utf-8").upper()
            for marker in ("BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY",
                           "BEGIN EC PRIVATE KEY", "BEGIN CERTIFICATE"):
                self.assertNotIn(marker, blob, f"{path.name} contains {marker}")

    def test_no_engine_configuration_file_was_copied_wholesale(self):
        """`mailcow.conf` carries database and API credentials. It is never versioned."""
        for forbidden in ("mailcow.conf", ".env", "id_rsa", "key.pem", "cert.pem"):
            self.assertFalse(
                (ENGINE_DIR / forbidden).exists(),
                f"{forbidden} must never be versioned",
            )


class GatewayComposeContractTest(SimpleTestCase):
    def setUp(self):
        with open(OVERRIDE, encoding="utf-8") as fh:
            self.compose = yaml.safe_load(fh)
        self.svc = self.compose["services"][SERVICE]

    # ── image pinning ───────────────────────────────────────────────────────

    def test_the_image_is_pinned_to_an_explicit_version(self):
        image = self.svc["image"]
        self.assertRegex(
            image, r"^haproxy:\d+\.\d+\.\d+-",
            "the gateway image must name an explicit point release",
        )

    def test_the_image_is_also_pinned_by_digest(self):
        """
        A tag can be moved. The digest is what makes the deployed bytes
        reproducible, and this process sits in the path of customer mail.
        """
        self.assertRegex(self.svc["image"], r"@sha256:[0-9a-f]{64}$")

    def test_the_image_is_not_floating(self):
        for floating in (":latest", ":stable", ":alpine\n", ":3-alpine"):
            self.assertNotIn(floating, self.svc["image"])

    # ── exposure ────────────────────────────────────────────────────────────

    def test_the_gateway_publishes_no_host_port(self):
        """
        Publishing one rebuilds the exact hole this gateway replaces: a bind
        address restricts the destination, never the source.
        """
        self.assertIsNone(
            self.svc.get("ports"),
            "the gateway must not publish a host port",
        )

    def test_the_gateway_does_not_use_host_networking(self):
        self.assertNotEqual(self.svc.get("network_mode"), "host")

    def test_the_config_is_mounted_read_only(self):
        volumes = self.svc.get("volumes") or []
        self.assertTrue(volumes)
        for volume in volumes:
            self.assertIn(":ro", volume, f"{volume} is writable")

    # ── networks ────────────────────────────────────────────────────────────

    def test_the_gateway_joins_exactly_two_networks(self):
        self.assertEqual(set(self.svc["networks"]), {ENGINE_NET, LINK})

    def test_the_engine_hostname_alias_exists_only_on_the_private_link(self):
        """
        The alias is what makes MateMail's hostname resolve. On the engine's own
        network it would shadow nothing useful and could confuse engine-internal
        resolution of the real host.
        """
        self.assertIn(ENGINE_HOST, self.svc["networks"][LINK]["aliases"])
        engine_side = self.svc["networks"][ENGINE_NET].get("aliases") or []
        self.assertNotIn(ENGINE_HOST, engine_side)

    def test_the_gateway_has_the_expected_static_engine_network_address(self):
        """
        The engine's API ACL is scoped to this address. A DHCP address would
        lock MateMail out of its own engine on the first recreation.
        """
        self.assertEqual(self.svc["networks"][ENGINE_NET]["ipv4_address"], GATEWAY_IP)

    def test_the_link_network_is_external(self):
        """
        Externally owned, so neither Compose project's lifecycle can delete a
        network the other still depends on.
        """
        self.assertTrue(self.compose["networks"][LINK]["external"])

    def test_the_override_declares_no_other_service(self):
        self.assertEqual(
            list(self.compose["services"]), [SERVICE],
            "the override must add the gateway and nothing else",
        )

    # ── operational ─────────────────────────────────────────────────────────

    def test_the_gateway_restarts_automatically(self):
        self.assertIn(self.svc.get("restart"), ("unless-stopped", "always"))

    def test_the_gateway_has_a_healthcheck_covering_both_listeners(self):
        probe = " ".join(str(x) for x in self.svc["healthcheck"]["test"])
        self.assertIn("8453", probe)
        self.assertIn("587", probe)


class HaproxyConfigContractTest(SimpleTestCase):
    def setUp(self):
        self.raw = HAPROXY.read_text(encoding="utf-8")
        # Comments explain the design at length; the directives are what runs.
        self.directives = "\n".join(
            line for line in self.raw.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

    def test_the_proxy_runs_in_tcp_mode(self):
        self.assertRegex(self.directives, r"(?m)^\s*mode\s+tcp\s*$")

    def test_no_http_mode_anywhere(self):
        """
        HTTP mode would parse and rewrite the stream, which for a TLS
        passthrough is both wrong and a silent downgrade of what MateMail
        believes it is talking to.
        """
        self.assertNotRegex(self.directives, r"(?m)^\s*mode\s+http\s*$")

    def test_the_api_port_forwards_to_the_engine_web_container(self):
        self.assertRegex(self.directives, r"bind\s+:8453")
        self.assertRegex(self.directives, r"server\s+\S+\s+nginx-mailcow:8453")

    def test_the_submission_port_forwards_to_the_engine_mta(self):
        self.assertRegex(self.directives, r"bind\s+:587")
        self.assertRegex(self.directives, r"server\s+\S+\s+postfix-mailcow:587")

    def test_tls_is_never_terminated_by_the_gateway(self):
        """
        The certificate MateMail validates must be the engine's own. A `ssl`
        keyword on a bind, or a crt file, means the gateway presents its own
        certificate and hostname verification stops meaning what it should.
        """
        self.assertNotRegex(self.directives, r"bind[^\n]*\sssl\b")
        self.assertNotIn("crt ", self.directives)
        self.assertNotIn("crt-list", self.directives)

    def test_the_gateway_does_not_originate_tls_to_the_upstreams(self):
        """Passthrough both ways: the engine terminates, the gateway forwards."""
        self.assertNotRegex(self.directives, r"server[^\n]*\sssl\b")

    def test_upstreams_are_re_resolved_rather_than_frozen_at_startup(self):
        """
        Engine container addresses change whenever that stack is recreated. A
        gateway holding a startup address fails in a way that looks exactly like
        the engine being down.
        """
        self.assertIn("resolvers", self.directives)
        self.assertIn("127.0.0.11", self.directives)
        for line in self.directives.splitlines():
            if line.strip().startswith("server "):
                self.assertIn("resolvers", line, f"upstream not re-resolved: {line.strip()}")

    def test_no_upstream_other_than_the_two_engine_services(self):
        servers = re.findall(r"(?m)^\s*server\s+\S+\s+(\S+)", self.directives)
        self.assertEqual(
            sorted(servers), ["nginx-mailcow:8453", "postfix-mailcow:587"]
        )


class HostSideEngineAssetsTest(SimpleTestCase):
    """
    Three host-side files are versioned alongside the gateway. Each is small,
    non-secret, and would materially harm recovery if lost — and none is
    regenerable from anything else on the host.
    """

    HOOK = ENGINE_DIR / "certbot-deploy-hook-mailcow-mx.sh"
    VHOST = ENGINE_DIR / "nginx-mx.matemail.online.conf"
    VARS = ENGINE_DIR / "vars.local.inc.php"

    def test_all_three_are_versioned(self):
        for path in (self.HOOK, self.VHOST, self.VARS):
            self.assertTrue(path.is_file(), f"{path.name} is missing")

    # ── certificate renewal hook ────────────────────────────────────────────

    def test_the_hook_only_acts_on_the_engine_certificate(self):
        """
        Certbot runs every deploy hook for every renewal. Without the lineage
        guard, renewing an unrelated certificate would restart mail services.
        """
        text = self.HOOK.read_text(encoding="utf-8")
        self.assertIn("RENEWED_LINEAGE", text)
        self.assertIn("mx.matemail.online", text)

    def test_the_hook_restarts_only_the_services_that_hold_the_certificate(self):
        text = self.HOOK.read_text(encoding="utf-8")
        for service in ("postfix-mailcow", "dovecot-mailcow", "nginx-mailcow"):
            self.assertIn(service, text)
        # Restarting the whole stack would turn a file copy into a mail outage.
        self.assertNotIn("docker compose restart\n", text)
        self.assertNotIn("docker compose down", text)

    def test_the_hook_installs_the_private_key_restrictively(self):
        self.assertIn("install -o root -g root -m 600", self.HOOK.read_text(encoding="utf-8"))

    def test_the_hook_never_prints_key_material(self):
        text = self.HOOK.read_text(encoding="utf-8")
        for leak in ("cat ", "echo $(cat", "privkey.pem\"", "openssl rsa"):
            if leak == 'privkey.pem"':
                continue
            self.assertNotIn(f"{leak}${{RENEWED_LINEAGE}}", text)

    # ── ACME vhost ──────────────────────────────────────────────────────────

    def test_the_vhost_serves_only_the_acme_challenge(self):
        text = self.VHOST.read_text(encoding="utf-8")
        self.assertIn("/.well-known/acme-challenge/", text)
        self.assertIn("return 404", text)

    def _vhost_directives(self):
        """
        Directives only. The file's comments explain at length *why* it proxies
        nothing, so matching raw text would fail on the explanation rather than
        on a real proxy_pass.
        """
        return "\n".join(
            line for line in self.VHOST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

    def test_the_vhost_proxies_nothing(self):
        """
        A proxy_pass here would publish the engine admin UI or API on the
        public interface — exactly what every other control in this design
        prevents.
        """
        directives = self._vhost_directives()
        for directive in ("proxy_pass", "fastcgi_pass", "uwsgi_pass", "grpc_pass"):
            self.assertNotIn(directive, directives, f"the ACME vhost contains {directive}")

    def test_the_vhost_listens_only_on_plain_http(self):
        """443 belongs to the applications; this vhost exists only for HTTP-01."""
        directives = self._vhost_directives()
        self.assertNotIn("listen 443", directives)
        self.assertNotIn("ssl_certificate", directives)
        self.assertIn("listen 80", directives)

    # ── DKIM private-key display ────────────────────────────────────────────

    def test_dkim_private_key_display_is_disabled(self):
        text = self.VARS.read_text(encoding="utf-8")
        self.assertRegex(
            text, r"\$SHOW_DKIM_PRIV_KEYS\s*=\s*false\s*;",
            "the engine would expose DKIM private keys through its API",
        )

    def test_it_is_never_set_true_anywhere_in_the_file(self):
        self.assertNotRegex(
            self.VARS.read_text(encoding="utf-8"),
            r"\$SHOW_DKIM_PRIV_KEYS\s*=\s*true",
        )

    def test_it_is_the_local_override_not_the_upstream_file(self):
        """
        `vars.inc.php` is replaced on every engine update; only
        `vars.local.inc.php` survives.
        """
        self.assertTrue(self.VARS.name.endswith("vars.local.inc.php"))


class GatewayReadmeTest(SimpleTestCase):
    """
    The README carries two operational facts that are not recoverable from the
    config files and whose absence causes real incidents.
    """

    def setUp(self):
        self.text = README.read_text(encoding="utf-8")

    def test_the_one_time_network_creation_is_documented_with_internal(self):
        self.assertIn("docker network create --internal matemail_engine_link", self.text)

    def test_it_does_not_imply_the_deploy_creates_the_network(self):
        self.assertIn("does not create this network", self.text.lower().replace("**", ""))

    def test_the_static_address_upgrade_check_is_documented(self):
        self.assertIn(GATEWAY_IP, self.text)
        lowered = self.text.lower()
        self.assertIn("before every", lowered)
        self.assertIn("upgrade", lowered)
