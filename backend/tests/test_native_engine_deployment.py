"""
The NE1 Native Engine deployment contract.

These pin the properties whose loss would be **silent**. A native stack that
mounts a mailcow volume, publishes a mail port, or pulls `latest` still starts
and still looks healthy — it just quietly undermines the live production engine
or the isolation the whole phase exists to establish.

mailcow is the LIVE engine holding the only customer mail path and the platform
sender's identity. The isolation assertions below are not tidiness; they are the
reason NE1 can be built on the same host at all.

Scope is deliberately NE1. There are no provisioning assertions here: NE2 owns
domains, mailboxes, aliases and DKIM, and testing them now would be testing
intentions.
"""
import pathlib
import re
import unittest

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
NE = REPO / "deploy" / "native-engine"
COMPOSE = NE / "docker-compose.yml"
ENV_EXAMPLE = NE / ".env.example"
API = REPO / "engine" / "native_api" / "app.py"

#: The ten services NE0.19 requires. Compose keys are short; container_name
#: carries the full `matemail-native-*` identity.
REQUIRED_SERVICES = {
    "postfix", "dovecot", "rspamd", "clamav", "olefy",
    "unbound", "db", "redis", "api", "policy",
}

#: Anything named like this belongs to the live engine and must never appear.
MAILCOW_MARKERS = ("mailcowdockerized", "mailcow-network", "vmail-vol", "crypt-vol")


def load_compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def directives_only(text: str) -> str:
    """Strip comments, so prose explaining a rule cannot satisfy a test for it."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


class StructureTest(unittest.TestCase):
    def test_the_deployment_project_exists(self):
        for path in (COMPOSE, ENV_EXAMPLE, API):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), f"missing {path}")

    def test_the_compose_file_parses(self):
        self.assertIsInstance(load_compose(), dict)

    def test_all_ten_services_are_defined(self):
        services = set(load_compose()["services"])
        self.assertEqual(
            services, REQUIRED_SERVICES,
            "NE0.19 defines exactly ten services; this file defines "
            f"{sorted(services)}",
        )

    def test_every_service_carries_the_native_identity(self):
        """
        A container named like mailcow's would be confusing at exactly the wrong
        moment — during an incident on a host running both engines.
        """
        for name, svc in load_compose()["services"].items():
            with self.subTest(service=name):
                self.assertTrue(
                    svc.get("container_name", "").startswith("matemail-native-"),
                    f"{name} has no matemail-native-* container_name",
                )


class IsolationFromMailcowTest(unittest.TestCase):
    """The assertions that make it safe to run this beside the live engine."""

    def setUp(self):
        self.raw = directives_only(COMPOSE.read_text(encoding="utf-8"))
        self.compose = load_compose()

    def test_no_mailcow_volume_or_network_is_referenced(self):
        for marker in MAILCOW_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(
                    marker, self.raw,
                    f"'{marker}' names live mailcow state — mounting or joining "
                    "it would couple the new engine to the running one",
                )

    def test_every_volume_is_native_and_new(self):
        for key, spec in (self.compose.get("volumes") or {}).items():
            with self.subTest(volume=key):
                name = (spec or {}).get("name", key)
                self.assertTrue(
                    name.startswith("matemail_native_"),
                    f"volume {name} is not namespaced to the native engine",
                )
                self.assertNotIn("external", spec or {},
                                 "NE1 volumes must be created by this project")

    def test_the_network_is_its_own(self):
        nets = self.compose["networks"]
        self.assertIn("engine", nets)
        self.assertEqual(nets["engine"]["name"], "matemail_native_engine")
        self.assertNotIn("external", nets["engine"])

    def test_the_engine_link_is_not_joined_during_ne1(self):
        """
        NE0 permits only `api` and `policy` on matemail_engine_link, and only
        from NE5. During NE1 the stack must reach neither MateMail nor mailcow.
        """
        self.assertNotIn("matemail_engine_link", self.raw)

    def test_no_bind_mount_escapes_the_project(self):
        """
        Two bind mounts deliberately reach outside `deploy/native-engine/` — the
        API source and the shared policy bridge. Both are repository files, both
        read-only. Anything else is suspect.
        """
        allowed = {"../../engine/native_api", "../../scripts/postfix_policy_bridge.py"}
        for name, svc in self.compose["services"].items():
            for vol in svc.get("volumes", []):
                if not isinstance(vol, str) or not vol.startswith("."):
                    continue
                source = vol.split(":")[0]
                if source.startswith("../"):
                    with self.subTest(service=name, mount=source):
                        self.assertIn(source, allowed)
                        self.assertTrue(vol.rstrip().endswith(":ro"),
                                        f"{source} must be mounted read-only")


class NoExposureTest(unittest.TestCase):
    """NE1 opens nothing. Not one port, not one host network."""

    def setUp(self):
        self.compose = load_compose()
        self.raw = directives_only(COMPOSE.read_text(encoding="utf-8"))

    def test_no_service_publishes_a_host_port(self):
        for name, svc in self.compose["services"].items():
            with self.subTest(service=name):
                self.assertNotIn(
                    "ports", svc,
                    f"{name} publishes a host port; NE1 opens none, and a mail "
                    "port would be reachable before any policy exists",
                )

    def test_no_host_networking(self):
        for forbidden in ("network_mode: host", 'network_mode: "host"'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.raw)

    def test_no_privileged_container_or_added_capability(self):
        for name, svc in self.compose["services"].items():
            with self.subTest(service=name):
                self.assertNotIn("privileged", svc)
                self.assertNotIn("cap_add", svc)


class ImagePolicyTest(unittest.TestCase):
    """Pinned by tag AND digest — an unattended image change is an outage."""

    def setUp(self):
        self.compose = load_compose()

    def test_no_service_uses_latest(self):
        for name, svc in self.compose["services"].items():
            with self.subTest(service=name):
                self.assertNotIn(":latest", svc["image"])

    def test_upstream_images_are_digest_pinned(self):
        """
        Services whose image comes from a public registry must name a digest.
        The three built under repository control are variable-driven until CI
        publishes them — see test_repo_built_images_are_variable_driven.
        """
        for name, svc in self.compose["services"].items():
            image = svc["image"]
            if image.startswith("${"):
                continue
            with self.subTest(service=name):
                self.assertIn("@sha256:", image, f"{name} is not digest-pinned")

    def test_repo_built_images_are_variable_driven(self):
        """
        Postfix, Unbound and Olefy have no acceptable upstream image, and
        Dovecot's cannot run the required identity model, so all four are
        are built under repository control and supplied by variable rather than
        pulled from an unvetted community source.
        """
        for name in ("postfix", "dovecot", "unbound", "olefy", "api"):
            with self.subTest(service=name):
                self.assertTrue(
                    self.compose["services"][name]["image"].startswith("${"),
                    f"{name} should come from a repository-controlled image",
                )

    def test_every_repo_built_image_has_a_dockerfile(self):
        for name in ("postfix", "dovecot", "unbound", "olefy", "api"):
            with self.subTest(service=name):
                self.assertTrue(
                    (NE / "images" / name / "Dockerfile").is_file(),
                    f"no Dockerfile under repository control for {name}",
                )


class OperationalContractTest(unittest.TestCase):
    def setUp(self):
        self.compose = load_compose()

    def test_every_service_has_a_healthcheck(self):
        for name, svc in self.compose["services"].items():
            with self.subTest(service=name):
                self.assertIn("healthcheck", svc, f"{name} has no healthcheck")

    def test_healthchecks_probe_behaviour_not_liveness(self):
        """
        "the process exists" is not health. Each probe must exercise the thing
        the service is for — a resolver that resolves, a database that answers.
        """
        expected = {
            "db": "pg_isready", "redis": "redis-cli", "unbound": "drill",
            "clamav": "clamdcheck", "rspamd": "rspamadm", "dovecot": "doveadm",
            "postfix": "postfix status", "api": "/health",
            "olefy": "10055", "policy": "10031",
        }
        for name, needle in expected.items():
            probe = " ".join(str(x) for x in self.compose["services"][name]["healthcheck"]["test"])
            with self.subTest(service=name):
                self.assertIn(needle, probe)

    def test_every_service_restarts_unless_stopped(self):
        for name, svc in self.compose["services"].items():
            with self.subTest(service=name):
                self.assertEqual(svc.get("restart"), "unless-stopped")

    def test_the_heavy_scanners_are_memory_bounded(self):
        """
        Seven unrelated production applications share this host. ClamAV holds
        ~1 GB resident and spikes on signature reload; an unbounded spike is how
        an unrelated service gets OOM-killed.
        """
        for name in ("clamav", "rspamd"):
            with self.subTest(service=name):
                self.assertIn("mem_limit", self.compose["services"][name])

    def test_state_bearing_services_have_a_volume(self):
        for name in ("db", "redis", "rspamd", "clamav", "unbound",
                     "dovecot", "postfix"):
            with self.subTest(service=name):
                self.assertTrue(self.compose["services"][name].get("volumes"),
                                f"{name} keeps state but mounts no volume")


class WriteModelTest(unittest.TestCase):
    """NE0.2 and NE0.21: exactly one direct writer to the engine database."""

    def setUp(self):
        self.compose = load_compose()
        self.api_source = API.read_text(encoding="utf-8")

    def test_only_the_api_holds_database_credentials(self):
        """
        Postfix and Dovecot get read-only roles at NE3. Neither may be handed
        the owner credential, and Django must never reach this database at all.
        """
        for name, svc in self.compose["services"].items():
            if name in ("db", "api"):
                continue
            env = svc.get("environment") or {}
            with self.subTest(service=name):
                self.assertNotIn("NATIVE_DB_PASSWORD", env,
                                 f"{name} must not hold the engine DB password")

    def test_the_bootstrap_creates_read_only_roles(self):
        sql = (NE / "postgres" / "init" / "001_bootstrap.sql").read_text(encoding="utf-8")
        self.assertIn("engine_ro_postfix", sql)
        self.assertIn("engine_ro_dovecot", sql)
        self.assertIn("GRANT SELECT", sql)
        self.assertNotIn("GRANT INSERT", sql)
        self.assertNotIn("GRANT UPDATE", sql)

    def test_engine_writes_live_where_they_are_reviewed(self):
        """
        NE1 asserted the API implemented NO provisioning, which was right then
        and is deliberately no longer true: NE2 IS the provisioning layer. The
        invariant that survives the phase change is the one that matters — every
        write to engine state goes through one reviewed module reached only via
        the API.

        `db.py` writes too, but only the migration ladder's own bookkeeping row,
        so it is allowed exactly that and checked for it.
        """
        engine = REPO / "engine" / "native_api"
        writers = {}
        for module in engine.glob("*.py"):
            body = module.read_text(encoding="utf-8")
            upper = body.upper()
            if any(verb in upper for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM")):
                writers[module.name] = body
        self.assertTrue(writers, "no writer found at all — the search is wrong")
        self.assertEqual(
            set(writers), {"provisioning.py", "db.py"},
            "engine writes must stay in provisioning.py (state) and db.py (migrations)",
        )
        # db.py may touch the version ledger and nothing else.
        for table in ("domain", "mailbox", "alias", "forwarding", "dkim_key"):
            with self.subTest(table=table):
                self.assertNotIn(f"INTO {table}", writers["db.py"])
                self.assertNotIn(f"FROM {table} ", writers["db.py"])
        self.assertIn("INSERT INTO schema_version", writers["db.py"])

    def test_no_django_code_connects_to_the_engine_database(self):
        """
        NE0.2, checked against the source rather than trusted. A Django-side
        connection would create a second writer and end the invariant above.
        """
        apps_dir = REPO / "backend" / "apps"
        offenders = []
        for module in apps_dir.rglob("*.py"):
            body = module.read_text(encoding="utf-8", errors="ignore")
            if "NATIVE_DB_PASSWORD" in body or "matemail_engine" in body:
                offenders.append(str(module.relative_to(REPO)))
        self.assertEqual(offenders, [], f"Django reaches the engine database in: {offenders}")

    def test_the_api_fails_closed_without_its_secret(self):
        self.assertIn("if not API_SECRET:", self.api_source)
        self.assertIn("hmac.compare_digest", self.api_source)


class PolicyBridgeReuseTest(unittest.TestCase):
    def setUp(self):
        self.compose = load_compose()

    def test_the_proven_p5_bridge_is_reused_not_duplicated(self):
        mounts = " ".join(self.compose["services"]["policy"].get("volumes", []))
        self.assertIn("scripts/postfix_policy_bridge.py", mounts)

    def test_there_is_no_second_copy_of_the_bridge(self):
        self.assertFalse(
            (NE / "policy" / "postfix_policy_bridge.py").exists(),
            "the bridge must be reused from scripts/, not copied",
        )

    def test_ne1_does_not_point_the_bridge_at_production(self):
        """
        The live P5 bridge stays untouched. NE1's copy must talk to nothing.
        """
        env = self.compose["services"]["policy"]["environment"]
        url = env["DJANGO_INTERNAL_URL"]
        self.assertIn("127.0.0.1:1", url,
                      "NE1's policy bridge must default to an unreachable target")


class ResolverTest(unittest.TestCase):
    """NE0.18: DANE is only as good as the resolver behind it."""

    def setUp(self):
        self.compose = load_compose()
        self.unbound_conf = (NE / "unbound" / "unbound.conf").read_text(encoding="utf-8")

    def test_the_resolver_has_a_pinned_address(self):
        addr = self.compose["services"]["unbound"]["networks"]["engine"]["ipv4_address"]
        self.assertRegex(addr, r"^172\.27\.0\.\d+$")

    def test_mail_services_resolve_through_it(self):
        addr = self.compose["services"]["unbound"]["networks"]["engine"]["ipv4_address"]
        for name in ("postfix", "dovecot", "rspamd", "clamav"):
            with self.subTest(service=name):
                self.assertIn(addr, self.compose["services"][name].get("dns", []))

    def test_dnssec_validation_is_on_and_not_permissive(self):
        directives = directives_only(self.unbound_conf)
        self.assertIn("auto-trust-anchor-file", directives)
        self.assertIn("val-permissive-mode: no", directives,
                      "permissive mode would downgrade bogus answers instead of "
                      "failing them — the exact silent failure NE0.18 forbids")

    def test_the_resolver_is_not_open(self):
        self.assertIn("access-control: 0.0.0.0/0 refuse",
                      directives_only(self.unbound_conf))

    def test_the_resolver_probe_asks_this_resolver_and_checks_validation(self):
        """
        `unbound-host -r` reads /etc/resolv.conf and asks Docker's resolver, so
        it passes with Unbound completely dead — measured during NE1, not
        theorised. The probe must name this resolver and require the AD bit, so
        a resolver that answers without validating is also a failure.
        """
        probe = " ".join(
            str(x) for x in load_compose()["services"]["unbound"]["healthcheck"]["test"]
        )
        self.assertIn("@127.0.0.1", probe, "the probe must query THIS resolver")
        self.assertIn(" ad", probe, "the probe must require authenticated data")
        self.assertNotIn("unbound-host -r", probe)

    def test_the_trust_anchor_persists(self):
        mounts = " ".join(self.compose["services"]["unbound"]["volumes"])
        self.assertIn("native_unbound:/var/lib/unbound", mounts)


class MalwareScanningTest(unittest.TestCase):
    """NE0.17: both scanners retained, and a scanner failure defers."""

    def test_clamav_and_olefy_are_present(self):
        services = load_compose()["services"]
        self.assertIn("clamav", services)
        self.assertIn("olefy", services)

    def test_rspamd_points_at_the_native_scanners(self):
        av = (NE / "rspamd" / "local.d" / "antivirus.conf").read_text(encoding="utf-8")
        ext = (NE / "rspamd" / "local.d" / "external_services.conf").read_text(encoding="utf-8")
        self.assertIn("clamav:3310", av)
        self.assertIn("olefy:10055", ext)

    def test_rspamd_defers_rather_than_passing_unscanned_mail(self):
        opts = (NE / "rspamd" / "local.d" / "options.inc").read_text(encoding="utf-8")
        self.assertIn("soft_reject_on_timeout = true", directives_only(opts))

    def test_clamav_has_its_own_signature_database(self):
        mounts = " ".join(load_compose()["services"]["clamav"]["volumes"])
        self.assertIn("native_clamav_db", mounts)
        self.assertNotIn("clamd-db-vol", mounts)


class MailStoreTest(unittest.TestCase):
    """NE0.4: layout matches mailcow's so NE6 can copy the platform sender."""

    def setUp(self):
        self.dovecot = (NE / "dovecot" / "dovecot.conf").read_text(encoding="utf-8")

    def test_vmail_uid_gid_matches_the_engine_it_will_replace(self):
        directives = directives_only(self.dovecot)
        self.assertIn("mail_uid = 5000", directives)
        self.assertIn("mail_gid = 5000", directives)

    def test_maildir_layout_matches(self):
        """
        Dovecot 2.4 renamed the variables — %d/%n became %{user | domain} and
        %{user | username} — but the LAYOUT is what NE6's verbatim copy depends
        on, not the spelling.
        """
        directives = directives_only(self.dovecot)
        self.assertIn("/var/vmail/%{user | domain}/%{user | username}", directives)
        self.assertIn("mail_driver = maildir", directives)

    def test_quota_does_not_depend_on_the_database(self):
        """
        A mailbox that cannot be told it is full is a disk that fills. Storage
        quota keeps enforcing when the engine database does not.

        2.4 replaced `quota = maildir:...` with a `quota storage` block layered
        over the mail driver, which is maildir here. The property under test is
        unchanged: no SQL dict may sit between a mailbox and its limit.
        """
        directives = directives_only(self.dovecot)
        self.assertIn("quota = yes", directives)
        self.assertRegex(directives, r"quota storage\s*\{[^}]*quota_storage_size")
        for backend in ("dict", "sql"):
            with self.subTest(backend=backend):
                self.assertNotRegex(directives, rf"quota\s+{backend}\s*\{{")

    def test_native_mail_volumes_are_separate(self):
        mounts = " ".join(load_compose()["services"]["dovecot"]["volumes"])
        self.assertIn("native_vmail:", mounts)
        self.assertIn("native_vmail_index:", mounts)


class AntiRelayFromTheStartTest(unittest.TestCase):
    """Defaults that are far cheaper to keep than to retrofit."""

    def setUp(self):
        self.main_cf = directives_only((NE / "postfix" / "main.cf").read_text(encoding="utf-8"))

    def test_relay_restrictions_are_present_from_ne1(self):
        self.assertIn("defer_unauth_destination", self.main_cf)
        self.assertIn("reject_unauth_destination", self.main_cf)

    def test_mynetworks_is_narrow(self):
        """
        mailcow's mynetworks spans its whole container subnet, which is why
        loopback SMTP there short-circuits before any policy hook. Starting
        narrow means NE3's hook is actually reached.
        """
        self.assertIn("mynetworks_style = host", self.main_cf)
        self.assertNotIn("172.27.0.0/16", self.main_cf)

    def test_dane_and_dnssec_are_configured(self):
        self.assertIn("smtp_dns_support_level = dnssec", self.main_cf)
        self.assertIn("smtp_tls_security_level = dane", self.main_cf)

    def test_the_banner_does_not_name_the_software(self):
        match = re.search(r"^smtpd_banner\s*=\s*(.+)$", self.main_cf, re.M)
        self.assertIsNotNone(match)
        for leak in ("postfix", "debian", "ubuntu", "mailcow"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, match.group(1).lower())


class SecretsTest(unittest.TestCase):
    def test_the_template_carries_names_but_no_values(self):
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                with self.subTest(key=key):
                    self.assertEqual(
                        value.strip(), "" if key not in (
                            "NATIVE_DB_NAME", "NATIVE_DB_USER") else value.strip(),
                        f"{key} must not ship a value",
                    )

    def test_required_secrets_have_no_compose_default(self):
        raw = COMPOSE.read_text(encoding="utf-8")
        for var in ("NATIVE_DB_PASSWORD", "NATIVE_API_SECRET"):
            with self.subTest(var=var):
                self.assertIn(f"${{{var}:?required}}", raw,
                              f"{var} must be required, not defaulted")

    def test_no_secret_value_is_committed(self):
        for path in (COMPOSE, ENV_EXAMPLE, API):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r"(?i)password\s*[=:]\s*['\"][A-Za-z0-9]{8,}")


class DovecotDeniesAllAuthenticationTest(unittest.TestCase):
    """
    NE1 must be able to authenticate nobody at all.

    An earlier draft used `passdb { driver = static; args = nopassword=y }`,
    which accepts ANY password for ANY user. That is not a placeholder, it is an
    open door — the moment a port is published or the container is reachable,
    every mailbox is readable by anyone who guesses a name. These tests exist so
    it cannot quietly return.
    """

    def setUp(self):
        self.directives = directives_only(
            (NE / "dovecot" / "dovecot.conf").read_text(encoding="utf-8"))
        self.users = (NE / "dovecot" / "users").read_text(encoding="utf-8")

    def test_no_passwordless_acceptance(self):
        for forbidden in ("nopassword", "skip_password_check", "allow_all_users"):
            with self.subTest(setting=forbidden):
                self.assertNotIn(forbidden, self.directives)

    def test_no_accept_all_static_passdb(self):
        self.assertNotRegex(self.directives, r"passdb\s+static\b")
        self.assertNotRegex(self.directives, r"driver\s*=\s*static")

    def test_auth_is_backed_by_the_empty_passwd_file(self):
        self.assertRegex(self.directives, r"passdb\s+passwd-file\s*\{")
        self.assertIn("passwd_file_path = /etc/dovecot/users", self.directives)

    def test_the_passwd_file_holds_no_accounts(self):
        """One test account here would outlive the phase that added it."""
        for line in self.users.splitlines():
            entry = line.strip()
            if entry and not entry.startswith("#"):
                self.fail(f"passwd-file must hold no accounts, found: {entry!r}")

    def test_the_three_process_identities_are_separate(self):
        """
        Dovecot's normal privilege model, which NE1 owns: master starts as root,
        pre-auth login runs as an identity that owns no mail, internal services
        run as a third, and only mail access runs as vmail.

        An earlier NE1 draft collapsed all three onto vmail because the upstream
        image ships no `dovecot` or `dovenull` at all. That is precisely why the
        engine now builds its own Dovecot image.
        """
        self.assertIn("default_internal_user = dovecot", self.directives)
        self.assertIn("default_internal_group = dovecot", self.directives)
        self.assertIn("default_login_user = dovenull", self.directives)

    def test_the_preauth_identity_is_not_the_mail_user(self):
        """
        Pre-auth login handles untrusted bytes straight off the network. Giving
        it the identity that owns every customer mailbox removes the whole point
        of separating them.
        """
        for setting in ("default_login_user", "default_internal_user"):
            with self.subTest(setting=setting):
                self.assertNotIn(setting + " = vmail", self.directives)


class PostfixIPProtocolTest(unittest.TestCase):
    """IPv6 mail is a deliberate decision, never a default."""

    def setUp(self):
        self.main_cf = directives_only(
            (NE / "postfix" / "main.cf").read_text(encoding="utf-8"))

    def test_ipv6_is_not_silently_enabled(self):
        """
        Sending over IPv6 needs its own DNS, PTR and reputation decisions. A
        foundation that turns it on by default makes those decisions by
        accident, and the first symptom is mail rejected for a missing PTR.

        This constrains Postfix transport and listening only — Unbound still
        resolves AAAA records.
        """
        self.assertIn("inet_protocols = ipv4", self.main_cf)
        for wider in ("all", "ipv6"):
            with self.subTest(value=wider):
                self.assertNotRegex(
                    self.main_cf, rf"inet_protocols\s*=\s*{wider}\b")


class DovecotImageContractTest(unittest.TestCase):
    """
    The Dovecot image is an NE1 foundation property, not an NE3 decision.

    NE0.4 chose vmail 5000:5000 for mailcow compatibility, the NE6
    platform-sender maildir migration and unambiguous storage ownership. The
    upstream image ships vmail at 1000:1000 and runs unprivileged as that user,
    which puts uid 5000 out of reach — measured, not predicted:
    "setgid(5000 from userdb lookup) failed with euid=1000". These tests pin the
    contract so the architecture cannot be quietly downgraded to 1000 instead.
    """

    def setUp(self):
        self.directives = directives_only(
            (NE / "images" / "dovecot" / "Dockerfile").read_text(encoding="utf-8"))
        self.compose = load_compose()

    def test_the_image_is_repository_controlled(self):
        self.assertTrue(
            self.compose["services"]["dovecot"]["image"].startswith("${"),
            "dovecot must not be pulled straight from the upstream registry",
        )

    def test_vmail_is_remapped_to_5000(self):
        self.assertIn("groupmod -g 5000 vmail", self.directives)
        self.assertIn("usermod  -u 5000 -g 5000 vmail", self.directives)

    def test_the_mail_store_is_owned_by_5000(self):
        """
        Compares whole destination paths, not substrings: "/var/vmail" occurs
        inside "/var/vmail_index", so a substring check passes even when the
        mail store itself has been handed to the wrong uid. Caught by mutation.
        """
        owned = {
            line.split()[-1] for line in self.directives.splitlines()
            if line.startswith("COPY --from=layout") and "--chown=5000:5000" in line
        }
        for path in ("/var/vmail", "/var/vmail_index"):
            with self.subTest(path=path):
                self.assertIn(path, owned)

    def test_the_service_identities_are_created(self):
        for identity in ("dovecot", "dovenull"):
            with self.subTest(identity=identity):
                self.assertIn("groupadd --system " + identity, self.directives)
                self.assertIn("nologin " + identity, self.directives)

    def test_the_master_can_drop_privileges(self):
        """
        Dovecot drops from root to dovenull, dovecot and vmail. An unprivileged
        master can do neither half of that: it cannot fchown /run/dovecot/login,
        and it cannot reach uid 5000. Both were measured on the upstream image.
        """
        users = [line.split()[1] for line in self.directives.splitlines()
                 if line.startswith("USER ")]
        self.assertTrue(users, "the image sets no USER at all")
        # The LAST directive is the one that takes effect. Asserting merely that
        # "USER root" appears somewhere passes even when a later `USER vmail`
        # overrides it and puts the master back where it cannot drop privileges.
        # Caught by mutation.
        self.assertEqual(users[-1], "root")

    def test_it_derives_from_the_pinned_upstream_digest(self):
        """
        A derivative, not a rebuild: the Dovecot binaries stay byte-for-byte
        identical to upstream, which is the artifact actually worth trusting.
        """
        self.assertIn(
            "FROM dovecot/dovecot:2.4.1@sha256:"
            "1296e0f1029cdd95e6849fb82f5d142a6e2a46218451773316cea678de75254b",
            self.directives)

    def test_the_container_is_not_privileged(self):
        """
        Root inside an ordinary container is Dovecot's own model and needs no
        extra powers. `privileged`, host networking or added capabilities would
        each hand the mail engine far more than it requires.
        """
        svc = self.compose["services"]["dovecot"]
        for dangerous in ("privileged", "cap_add", "network_mode", "pid"):
            with self.subTest(key=dangerous):
                self.assertNotIn(dangerous, svc)


class NativeApiImageTest(unittest.TestCase):
    """
    NE2 gave the API its own image, and the reason is worth pinning.

    Python 3.13 REMOVED the `crypt` module, `hashlib` has no bcrypt, and the
    stock base image ships neither `bcrypt` nor `cryptography`. So a stock
    interpreter cannot produce the BLF-CRYPT hash the pinned Dovecot expects,
    and cannot generate an RSA key for DKIM. Measured, not assumed.
    """

    def setUp(self):
        self.dockerfile = (NE / "images" / "api" / "Dockerfile").read_text(encoding="utf-8")
        self.directives = directives_only(self.dockerfile)
        self.requirements = (NE / "images" / "api" / "requirements.txt").read_text(encoding="utf-8")
        self.compose = load_compose()

    def test_the_api_uses_a_repository_controlled_image(self):
        self.assertTrue(
            self.compose["services"]["api"]["image"].startswith("${"),
            "the API must not run on a stock image it cannot hash passwords with",
        )

    def test_dependencies_are_pinned_exactly(self):
        """A range here means the image CI publishes is not the one validated."""
        pinned = [line.strip() for line in directives_only(self.requirements).splitlines()
                  if line.strip()]
        self.assertTrue(pinned, "no dependencies declared")
        for line in pinned:
            with self.subTest(dependency=line):
                self.assertIn("==", line, f"{line} is not pinned to an exact version")

    def test_it_carries_the_three_dependencies_and_no_more(self):
        names = sorted(line.split("==")[0].split("[")[0].strip()
                       for line in directives_only(self.requirements).splitlines()
                       if line.strip())
        self.assertEqual(names, ["bcrypt", "cryptography", "psycopg"])

    def test_the_uid_matches_rspamd(self):
        """
        MEASURED: /var/lib/rspamd is drwxr-x--- 11333:11333 in rspamd/rspamd:3.11.
        A DKIM key must be mode 0600 AND readable by Rspamd at NE3, which is
        only possible if the writer and the reader share a uid.

        An earlier draft ran as `nobody` and could not even list the directory.
        """
        self.assertIn("USER 11333:11333", self.directives)
        self.assertIn("11333", self.directives)

    def test_the_compose_file_sets_no_command(self):
        """
        The image has an ENTRYPOINT. Compose `command` would be APPENDED to it,
        not replace it, producing `python3 -u app.py python3 -u app.py`.
        """
        self.assertNotIn("command", self.compose["services"]["api"])

    def test_the_api_no_longer_mounts_the_whole_rspamd_state(self):
        """
        NE2 narrowed this. The API needs one subdirectory to do DKIM; mounting
        all of native_rspamd gave it write access to Rspamd's bayes database
        too.
        """
        mounts = " ".join(self.compose["services"]["api"]["volumes"])
        self.assertIn("native_dkim:/var/lib/rspamd/dkim", mounts)
        self.assertNotIn("native_rspamd:/var/lib/rspamd", mounts)

    def test_the_api_does_not_bind_mount_its_own_source(self):
        """
        The digest must cover the CODE, not just the dependencies.

        NE1 and NE2A mounted `engine/native_api` over `/opt/matemail/native_api`.
        Once the API became a digest-pinned image that bind mount overlaid the
        very code the digest existed to guarantee: the pin then attested to
        bcrypt, cryptography and psycopg, and to nothing about the provisioning
        logic, the DKIM lifecycle or the migrations actually executing.

        An image whose digest does not cover its own code is not a pinned
        deployment.
        """
        mounts = self.compose["services"]["api"].get("volumes", [])
        for mount in mounts:
            spec = mount if isinstance(mount, str) else (
                str(mount.get("source", "")) + ":" + str(mount.get("target", "")))
            with self.subTest(mount=spec):
                self.assertNotIn("native_api", spec,
                                 "the API must not be handed its own source")
                self.assertNotIn("/opt/matemail/native_api", spec)

    def test_the_api_mounts_no_repository_source_at_all(self):
        """
        Not just native_api: any host path into the API is a way for code on the
        VPS to differ from the code in the pinned image.
        """
        for mount in self.compose["services"]["api"].get("volumes", []):
            spec = mount if isinstance(mount, str) else str(mount.get("source", ""))
            with self.subTest(mount=spec):
                self.assertFalse(
                    spec.startswith("./") or spec.startswith("../") or spec.startswith("/"),
                    f"the API must mount no host path, found {spec!r}",
                )

    def test_the_api_still_mounts_the_dkim_volume(self):
        """Removing the source mount must not take the key store with it."""
        mounts = " ".join(
            m if isinstance(m, str) else f"{m.get('source','')}:{m.get('target','')}"
            for m in self.compose["services"]["api"].get("volumes", [])
        )
        self.assertIn("native_dkim", mounts)
        self.assertIn("/var/lib/rspamd/dkim", mounts)

    def test_the_api_image_comes_from_the_variable(self):
        self.assertIn("NATIVE_API_IMAGE", self.compose["services"]["api"]["image"])

    def test_the_api_image_fallback_names_no_unpublished_tag(self):
        """
        The default used to be `:ne2`, which the workflow never publishes — it
        publishes the commit SHA and the moving `ne1`. A fallback pointing at a
        tag that does not exist fails obscurely; requiring the variable says
        exactly what is missing.
        """
        image = self.compose["services"]["api"]["image"]
        workflow = (REPO / ".github" / "workflows" / "native-engine-images.yml").read_text(
            encoding="utf-8")
        published = {line.strip().rsplit(":", 1)[-1]
                     for line in workflow.splitlines()
                     if "matemail-native-${{ matrix.component }}:" in line}
        default = image.split(":-", 1)[1].rstrip("}") if ":-" in image else None
        if default is not None:
            tag = default.rsplit(":", 1)[-1]
            self.assertIn(tag, published,
                          f"the Compose default names tag {tag!r}, which the "
                          f"workflow does not publish (it publishes {published})")

    def test_the_image_bakes_in_the_source_and_migrations(self):
        """The other half of the same guarantee: it must actually be in there."""
        self.assertIn("COPY engine/native_api /opt/matemail/native_api", self.directives)
        # Built from the repository root, or that COPY cannot resolve.
        workflow = (REPO / ".github" / "workflows" / "native-engine-images.yml").read_text(
            encoding="utf-8")
        self.assertIn("matrix.component == 'api'", workflow)
        self.assertIn("file: deploy/native-engine/images/${{ matrix.component }}/Dockerfile",
                      workflow)
        self.assertIn("COPY deploy/native-engine/images/api/requirements.txt",
                      self.directives)

    def test_rspamd_gets_the_keys_read_only(self):
        """
        Rspamd signs and verifies; it never creates, rotates or deletes a key.
        Read-only makes matemail-native-api's ownership structural.
        """
        mounts = " ".join(self.compose["services"]["rspamd"]["volumes"])
        self.assertIn("native_dkim:/var/lib/rspamd/dkim:ro", mounts)

    def test_the_dkim_path_still_matches_what_rspamd_interpolates(self):
        signing = (NE / "rspamd" / "local.d" / "dkim_signing.conf").read_text(encoding="utf-8")
        self.assertIn("/var/lib/rspamd/dkim/$domain.$selector.key", signing)

    def test_the_workflow_builds_it(self):
        workflow = (REPO / ".github" / "workflows" / "native-engine-images.yml").read_text(
            encoding="utf-8")
        self.assertIn("api", workflow)
        data = yaml.safe_load(workflow)
        matrix = data["jobs"]["build"]["strategy"]["matrix"]["component"]
        self.assertIn("api", matrix)
        for expected in ("postfix", "dovecot", "unbound", "olefy"):
            self.assertIn(expected, matrix)


class EngineSchemaContractTest(unittest.TestCase):
    """Properties of the NE2 schema that must not be edited away."""

    def setUp(self):
        self.sql = (REPO / "engine" / "native_api" / "migrations"
                    / "002_provisioning.sql").read_text(encoding="utf-8")
        self.directives = directives_only(self.sql)

    def test_every_expected_table_is_created(self):
        for table in ("domain", "mailbox", "alias", "alias_destination",
                      "forwarding", "dkim_key"):
            with self.subTest(table=table):
                self.assertIn(f"CREATE TABLE IF NOT EXISTS {table} ", self.directives)

    def test_forwarding_is_not_part_of_the_alias_tables(self):
        """
        The structural reason forwarding cannot become a sending right: it is a
        different table, so the send-as query cannot reach it even by mistake.
        """
        start = self.directives.index("CREATE TABLE IF NOT EXISTS forwarding")
        end = self.directives.index(");", start)
        block = self.directives[start:end]
        self.assertNotIn("alias", block)

    def test_alias_destination_carries_the_send_as_marker(self):
        start = self.directives.index("CREATE TABLE IF NOT EXISTS alias_destination")
        end = self.directives.index(");", start)
        block = self.directives[start:end]
        self.assertIn("mailbox_id", block)
        self.assertIn("REFERENCES mailbox(id)", block)

    def test_dkim_key_does_not_reference_domain(self):
        """
        Deliberate. The adapter contract requires a key to OUTLIVE its domain,
        and MateMail's deprovisioning task deletes the key explicitly first.
        A cascade here would break both.
        """
        start = self.directives.index("CREATE TABLE IF NOT EXISTS dkim_key")
        end = self.directives.index(");", start)
        block = self.directives[start:end]
        self.assertNotIn("REFERENCES domain", block)

    def test_the_private_key_is_a_path_and_not_a_value(self):
        self.assertIn("private_key_path", self.directives)
        self.assertNotIn("private_key text", self.directives)
        self.assertNotIn("private_key bytea", self.directives)

    def test_read_only_roles_cannot_write(self):
        self.assertIn("REVOKE INSERT, UPDATE, DELETE, TRUNCATE", self.directives)
        for role in ("engine_ro_postfix", "engine_ro_dovecot"):
            with self.subTest(role=role):
                self.assertIn(role, self.directives)

    def test_quota_columns_name_their_unit(self):
        """MB, never bytes. A column called `quota` would be an invitation."""
        for column in ("default_quota_mb", "max_quota_mb", "total_quota_mb", "quota_mb"):
            with self.subTest(column=column):
                self.assertIn(column, self.directives)

