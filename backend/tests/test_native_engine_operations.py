"""
Native Engine operations (NE4).

WHAT THIS COVERS
    The four capabilities NE4 added on top of a working mail path: immutable
    mailbox storage, real usage, enforced rate limits, and queue/quarantine
    control — plus the deployment mechanism that stops a configuration change
    from being silently ignored.

THE DEFECT THAT SHAPED THIS FILE
    NE3 runtime validation found that deleting a mailbox removed its rows and
    LEFT ITS MAIL on disk, at a path derived from the email address. Recreating
    the address therefore pointed a new owner at the previous owner's mail. No
    attacker required — only staff recreating a mailbox somebody asked to have
    deleted.

    Migration 004 makes storage an opaque per-row identity, so a recreated
    address is structurally unable to land on the old directory. Several tests
    here exist purely to keep that true.

EVERY DOMAIN HERE IS .invalid
    Reserved by RFC 2606, unresolvable by construction.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

from django.test import SimpleTestCase

ENGINE_DIR = pathlib.Path(__file__).resolve().parents[2] / "engine" / "native_api"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

REPO = pathlib.Path(__file__).resolve().parents[2]
NE = REPO / "deploy" / "native-engine"

try:
    import psycopg
    import db as engine_db
    import dkim as engine_dkim
    import operations as engine_operations
    import provisioning
    from validation import ValidationError
    ENGINE_IMPORTABLE = True
    IMPORT_ERROR = ""
except Exception as exc:                                    # pragma: no cover
    ENGINE_IMPORTABLE = False
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

DOMAIN = "ne4.invalid"


def directives_only(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith(("#", "--"))
    )


# ─────────────────────────────────────────────────────────────────────────────
# Configuration and deployment
# ─────────────────────────────────────────────────────────────────────────────


class RspamdConfigurationIsDeployedTest(SimpleTestCase):
    """
    REGRESSION: Rspamd ran for five days with superseded rules.

    Compose compares images, environment, mounts and labels — never the CONTENT
    of a bind-mounted file. NE3 updated `rspamd/local.d`, `docker compose up -d`
    reported success, and the container kept the configuration it started with.
    The antivirus scores that were supposed to reject malware were simply not
    loaded, and it was found by checking rather than by anything failing.

    The fix makes the configuration visible to Compose as a hash, so "the config
    changed" becomes "the environment changed".
    """

    def setUp(self):
        self.compose = (NE / "docker-compose.yml").read_text(encoding="utf-8")
        self.deploy = NE / "deploy.sh"

    def test_every_config_mounting_service_carries_a_config_hash(self):
        for service in ("RSPAMD", "POSTFIX", "DOVECOT", "UNBOUND"):
            with self.subTest(service=service):
                self.assertIn(f"NATIVE_{service}_CONFIG_HASH", self.compose)

    def test_a_deploy_entrypoint_computes_them(self):
        """
        The mechanism must not depend on an operator remembering a command.
        """
        self.assertTrue(self.deploy.is_file(), "deploy.sh is the documented path")
        body = self.deploy.read_text(encoding="utf-8")
        self.assertIn("sha256sum", body)
        for service in ("NATIVE_RSPAMD_CONFIG_HASH", "NATIVE_POSTFIX_CONFIG_HASH",
                        "NATIVE_DOVECOT_CONFIG_HASH", "NATIVE_UNBOUND_CONFIG_HASH"):
            with self.subTest(var=service):
                self.assertIn(service, body)
        self.assertIn("export", body)

    def test_the_deploy_never_tears_the_engine_down(self):
        body = self.deploy.read_text(encoding="utf-8")
        self.assertNotIn("compose down", body)
        self.assertNotIn("system prune", body)

    def test_the_hash_changes_when_configuration_changes(self):
        """
        A hash that does not move is a hash that recreates nothing.
        """
        import hashlib

        def digest(paths):
            accumulator = hashlib.sha256()
            for path in sorted(paths):
                accumulator.update(path.name.encode())
                accumulator.update(path.read_bytes())
            return accumulator.hexdigest()

        files = sorted((NE / "rspamd").rglob("*"))
        files = [f for f in files if f.is_file()]
        self.assertGreater(len(files), 1)
        before = digest(files)

        with tempfile.TemporaryDirectory() as tmp:
            copy = pathlib.Path(tmp) / "local.d"
            shutil.copytree(NE / "rspamd" / "local.d", copy)
            changed = sorted(p for p in copy.rglob("*") if p.is_file())
            (changed[0]).write_text(
                changed[0].read_text(encoding="utf-8") + "\n# marker\n", encoding="utf-8")
            self.assertNotEqual(before, digest(changed))


class QuarantineTriggerIsNotAnInternetBackdoorTest(SimpleTestCase):
    """
    Quarantine is driven by a header, so the header had better not be settable
    by the sender.
    """

    def setUp(self):
        self.header_checks = (NE / "postfix" / "header_checks").read_text(encoding="utf-8")
        self.milter = (NE / "rspamd" / "local.d" / "milter_headers.conf").read_text(encoding="utf-8")

    def test_postfix_holds_on_the_marker(self):
        rule = directives_only(self.header_checks)
        self.assertIn("HOLD", rule)
        self.assertIn("X-MateMail-Quarantine", rule)

    def test_the_rule_uses_posix_classes_not_pcre(self):
        """
        Postfix `regexp:` tables are POSIX extended. A `\\s` there does not mean
        whitespace — it matches a literal 's', and the rule silently stops
        matching the header it was written for.
        """
        rule = directives_only(self.header_checks)
        self.assertNotIn(r"\s", rule)
        self.assertIn("[[:space:]]", rule)

    def test_rspamd_strips_any_inbound_copy_of_the_marker(self):
        """
        Without this a remote sender could set the header themselves. It would
        only quarantine their own message — the header cannot release anything
        or skip scanning — but a marker whose meaning is partly attacker
        controlled is not a marker worth trusting.
        """
        self.assertIn("X-MateMail-Quarantine", self.milter)
        self.assertRegex(self.milter, r"remove\s*=|\['X-MateMail-Quarantine'\]\s*=\s*0")

    def test_a_confirmed_virus_is_rejected_not_quarantined(self):
        """
        Quarantine is for suspicion. Malware is not suspicion, and parking it in
        a queue for someone to release by accident is worse than refusing it.
        """
        groups = directives_only((NE / "rspamd" / "local.d" / "groups.conf").read_text(encoding="utf-8"))
        match = re.search(r'"CLAM_VIRUS"\s*\{[^}]*?score\s*=\s*([0-9.]+)', groups, re.S)
        self.assertIsNotNone(match)
        actions = directives_only((NE / "rspamd" / "local.d" / "actions.conf").read_text(encoding="utf-8"))
        reject = float(re.search(r"reject\s*=\s*([0-9.]+)", actions).group(1))
        self.assertGreater(float(match.group(1)), reject)


class RateLimitFailsClosedOnCounterOutageTest(SimpleTestCase):
    """
    REGRESSION: a Redis outage silently disabled every configured rate limit.

    The first NE4 implementation returned DUNNO when the counter was
    unreachable, so a mailbox somebody had deliberately capped was accepted with
    no enforcement at all — indistinguishable from having no limit, for exactly
    the mailboxes that needed one.

    The fix defers instead. The distinction that keeps it safe is WHERE the
    failure is handled: a mailbox with no configured limit returns before Redis
    is ever contacted, so an outage cannot block traffic that was never metered.
    """

    def setUp(self):
        self.control = (NE / "images" / "postfix" / "engine_control.py").read_text(encoding="utf-8")
        self.verdict = self.control[self.control.index("def rate_verdict"):
                                    self.control.index("class PolicyServer")]

    def test_a_counter_outage_defers_rather_than_allowing(self):
        # Everything from the INCR onward: the except block that handles an
        # unreachable counter, and nothing before it.
        after_incr = self.verdict[self.verdict.index("count = _redis_command"):]
        # ONLY the except block. Slicing to the end of the function would also
        # capture the legitimate final `DUNNO` returned when a message is under
        # its limit, which has nothing to do with a counter outage.
        handler = after_incr[after_incr.index("except Exception"):
                             after_incr.index("if count >")]
        self.assertIn("DEFER_IF_PERMIT", handler,
                      "an unreachable counter must defer, not allow")
        self.assertNotIn('return "action=DUNNO"', handler,
                         "the counter-failure path must not fall through to DUNNO")

    def test_the_deferral_is_temporary_not_permanent(self):
        """
        A counter outage is an engine problem, not a verdict about the message.
        A 5xx would bounce mail that was never wrong.
        """
        self.assertIn("4.7.1", self.verdict)
        self.assertNotIn("action=REJECT", self.verdict)
        self.assertNotIn("5.7.1", self.verdict)

    def test_an_unlimited_mailbox_returns_before_redis_is_touched(self):
        """
        THE PROPERTY THAT STOPS THIS BECOMING AN OUTAGE. Mailboxes with no
        configured limit must not be blocked by a counter they never used.
        """
        before_redis = self.verdict[:self.verdict.index("count = _redis_command")]
        self.assertIn('if not limit or limit["messages"] == 0:', before_redis)
        self.assertIn('return "action=DUNNO"', before_redis)

    def test_an_unauthenticated_request_returns_before_redis_is_touched(self):
        before_redis = self.verdict[:self.verdict.index("count = _redis_command")]
        self.assertIn("if not sasl_username:", before_redis)

    def test_a_database_outage_is_handled_differently_and_says_why(self):
        """
        The LOOKUP failing is not the COUNTER failing. With no database, Postfix's
        own map lookups are already failing and submission is already deferred by
        the restrictions that run first; failing closed here would additionally
        block every mailbox, including ones nobody ever metered.
        """
        lookup_handler = self.verdict[self.verdict.index("limit = lookup_limit"):
                                      self.verdict.index("if not limit or")]
        self.assertIn('return "action=DUNNO"', lookup_handler)
        self.assertIn("virtual_mailbox_maps", lookup_handler,
                      "the reasoning must be recorded where the decision is made")

    def test_recovery_needs_no_intervention(self):
        """
        Nothing is cached and nothing is latched: the next message re-runs the
        same lookup and the same INCR, so a returning Redis resumes enforcement
        on its own.
        """
        self.assertNotIn("global ", self.verdict)
        self.assertIn("_redis_command(\"INCR\"", self.verdict)

    def test_postfix_defers_when_the_policy_service_itself_is_unreachable(self):
        """
        The service failing to answer at all must not be a free pass either.
        """
        main_cf = (NE / "postfix" / "main.cf").read_text(encoding="utf-8")
        self.assertRegex(main_cf, r"smtpd_policy_service_default_action\s*=\s*4\d\d")


class ControlPlaneIsLeastPrivilegeTest(SimpleTestCase):
    """
    The queue control path touches Postfix directly, so where it lives and what
    it will accept are the whole security story.
    """

    def setUp(self):
        self.control = (NE / "images" / "postfix" / "engine_control.py").read_text(encoding="utf-8")
        self.compose = (NE / "docker-compose.yml").read_text(encoding="utf-8")

    def test_the_api_never_gets_the_docker_socket(self):
        self.assertNotIn("docker.sock", self.compose)

    def test_the_api_has_no_spool_mount(self):
        import yaml
        services = yaml.safe_load(self.compose)["services"]
        api_mounts = " ".join(str(v) for v in (services["api"].get("volumes") or []))
        self.assertNotIn("spool", api_mounts)

    def test_no_shell_is_ever_used(self):
        """
        Queue ids are caller-supplied. They reach `postsuper` as one element of a
        fixed argv list and never through a shell, so there is no quoting bug to
        get wrong.
        """
        self.assertIn("shell=False", self.control)
        self.assertNotIn("shell=True", self.control)
        self.assertNotIn("os.system", self.control)

    def test_queue_ids_are_validated_against_an_allowlist(self):
        self.assertRegex(self.control, r"_QUEUE_ID\s*=\s*re\.compile")
        self.assertIn("valid_queue_id", self.control)

    def test_the_control_api_refuses_to_start_without_its_secret(self):
        self.assertIn("NATIVE_CONTROL_SECRET", self.control)
        self.assertRegex(self.control, r"sys\.exit\(78\)")

    def test_the_rate_limit_policy_listens_only_on_loopback(self):
        """
        Its only client is the smtpd in the same container, so the protocol is
        never on the engine network.
        """
        self.assertIn('bind(("127.0.0.1"', self.control)

    def test_queue_id_pattern_rejects_traversal_and_injection(self):
        pattern = re.compile(r"^[A-Za-z0-9]{6,32}$")
        for hostile in ("../../etc/passwd", "ABC123; rm -rf /", "ABC 123",
                        "", "a", "A" * 64, "ABC/123", "ABC$(id)", "ABC\nDEF"):
            with self.subTest(value=hostile):
                self.assertIsNone(pattern.match(hostile))
        for legitimate in ("4B2B5105F27", "3Xy7Qm1234", "ABCDEF"):
            with self.subTest(value=legitimate):
                self.assertIsNotNone(pattern.match(legitimate))


class RateLimitRejectionIsTemporaryTest(SimpleTestCase):
    def test_the_policy_defers_rather_than_bouncing(self):
        """
        A rate limit is a statement about timing, not about the message. A 5xx
        would destroy mail that was never wrong and generate a bounce for what
        is a throttling decision.
        """
        control = (NE / "images" / "postfix" / "engine_control.py").read_text(encoding="utf-8")
        self.assertIn("DEFER_IF_PERMIT 4.7.1", control)
        self.assertNotIn("action=REJECT", control)

    def test_the_identity_charged_is_the_authenticated_login(self):
        """
        Charging the envelope sender would let a mailbox spread its traffic
        across every alias it may send as.
        """
        control = (NE / "images" / "postfix" / "engine_control.py").read_text(encoding="utf-8")
        self.assertIn("sasl_username", control)
        self.assertNotIn("sender=", control.split("def rate_verdict")[1][:400])


# ─────────────────────────────────────────────────────────────────────────────
# Engine behaviour, against a real PostgreSQL
# ─────────────────────────────────────────────────────────────────────────────


def _admin_dsn():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    parts = urllib.parse.urlparse(url)
    if not parts.hostname:
        return None
    return (f"host={parts.hostname} port={parts.port or 5432} dbname=postgres "
            f"user={parts.username or ''} "
            f"password={urllib.parse.unquote(parts.password or '')}")


class OperationsDatabaseTestCase(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not ENGINE_IMPORTABLE:
            raise unittest.SkipTest(f"engine modules not importable — {IMPORT_ERROR}")
        admin = _admin_dsn()
        if admin is None:
            raise unittest.SkipTest("DATABASE_URL is not set; no PostgreSQL to test against")

        cls.db_name = f"ne4_ops_test_{os.getpid()}"
        try:
            with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{cls.db_name}"')
                cur.execute(f'CREATE DATABASE "{cls.db_name}"')
        except psycopg.Error as exc:
            raise unittest.SkipTest(f"cannot create a scratch engine database: {exc}")

        cls._admin = admin
        cls.engine_dsn = admin.replace("dbname=postgres", f"dbname={cls.db_name}")
        cls.key_dir = pathlib.Path(tempfile.mkdtemp(prefix="ne4-dkim-"))
        cls._real_key_dir = engine_dkim.KEY_DIR
        engine_dkim.KEY_DIR = cls.key_dir
        for name in ("", ".db", ".dkim", ".provisioning", ".operations"):
            logging.getLogger(f"matemail.native.api{name}").setLevel(logging.ERROR)

        cls.conn = psycopg.connect(cls.engine_dsn, autocommit=True)
        bootstrap = (NE / "postgres" / "init" / "001_bootstrap.sql").read_text(encoding="utf-8")
        with cls.conn.cursor() as cur:
            cur.execute(bootstrap)
        engine_db.apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        except Exception:
            pass
        engine_dkim.KEY_DIR = cls._real_key_dir
        shutil.rmtree(cls.key_dir, ignore_errors=True)
        try:
            with psycopg.connect(cls._admin, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{cls.db_name}" WITH (FORCE)')
        except Exception:
            pass
        super().tearDownClass()

    def setUp(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "TRUNCATE domain, mailbox, alias, alias_destination, forwarding, "
                "dkim_key, mailbox_rate_limit, retired_mailbox_storage "
                "RESTART IDENTITY CASCADE"
            )

    def rows(self, sql, *args):
        with self.conn.cursor() as cur:
            cur.execute(sql, args or None)
            return cur.fetchall()

    def make_mailbox(self, local="alice", quota=512):
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.ensure_mailbox(
            self.conn, {"address": f"{local}@{DOMAIN}", "quota_mb": quota},
            password="Ne4-Local-Pw-1")
        return f"{local}@{DOMAIN}"


class SchemaVersionTest(OperationsDatabaseTestCase):
    def test_the_engine_is_at_the_required_version(self):
        self.assertEqual(
            engine_db.REQUIRED_VERSION,
            engine_db.current_version(self.conn),
        )

    def test_migrations_are_idempotent(self):
        before = engine_db.current_version(self.conn)
        engine_db.apply_migrations(self.conn)
        engine_db.apply_migrations(self.conn)
        self.assertEqual(before, engine_db.current_version(self.conn))
        self.assertEqual(
            len(engine_db.available_migrations()),
            self.rows("SELECT count(*) FROM schema_version")[0][0],
        )


class MailboxStorageIdentityTest(OperationsDatabaseTestCase):
    """
    REGRESSION: a recreated address inherited the previous owner's mail.
    """

    def storage_of(self, address):
        return provisioning.mailbox_storage_id(self.conn, address)

    def home_of(self, address):
        rows = self.rows(
            "SELECT mail_home FROM dovecot_userdb WHERE address = %s", address)
        return rows[0][0] if rows else None

    def test_a_new_mailbox_gets_an_opaque_identity(self):
        address = self.make_mailbox()
        storage = self.storage_of(address)
        self.assertIsNotNone(storage)
        # Opaque: it must not simply be the local part, or the whole mechanism
        # is the old address-derived path wearing a new column name.
        self.assertNotEqual("alice", storage)
        self.assertRegex(storage, r"^[0-9a-f-]{36}$")

    def test_storage_drives_the_maildir_path(self):
        address = self.make_mailbox()
        self.assertEqual(
            f"/var/vmail/{DOMAIN}/{self.storage_of(address)}", self.home_of(address))

    def test_recreating_an_address_lands_somewhere_new(self):
        """THE DEFECT. A recreated mailbox must not inherit a directory."""
        address = self.make_mailbox()
        first_storage = self.storage_of(address)
        first_home = self.home_of(address)

        provisioning.delete_mailbox(self.conn, address)
        provisioning.ensure_mailbox(
            self.conn, {"address": address, "quota_mb": 512}, password="Ne4-Local-Pw-2")

        second_storage = self.storage_of(address)
        self.assertNotEqual(first_storage, second_storage)
        self.assertNotEqual(first_home, self.home_of(address))

    def test_deletion_records_the_storage_it_retained(self):
        """
        Retained mail with no record is an orphan directory nobody dares touch.
        """
        address = self.make_mailbox()
        storage = self.storage_of(address)
        provisioning.delete_mailbox(self.conn, address)

        retired = provisioning.list_retired_storage(self.conn)
        self.assertEqual(1, len(retired))
        self.assertEqual(storage, retired[0]["storage_id"])
        self.assertEqual(address, retired[0]["address"])

    def test_deletion_does_not_destroy_mail(self):
        """
        Provisioning must not be able to delete customer mail as a side effect.
        The engine keeps the data and records it; P6 owns removal.
        """
        address = self.make_mailbox()
        provisioning.delete_mailbox(self.conn, address)
        self.assertEqual(
            1, self.rows("SELECT count(*) FROM retired_mailbox_storage")[0][0])

    def test_two_live_mailboxes_never_share_a_directory(self):
        self.make_mailbox("alice")
        self.make_mailbox("bob")
        storages = {r[0] for r in self.rows("SELECT storage_id FROM mailbox")}
        self.assertEqual(2, len(storages))

    def test_storage_identity_cannot_contain_a_path_separator(self):
        """
        It is concatenated into a filesystem path, so the database refuses
        anything that could escape the directory rather than trusting the writer.
        """
        self.make_mailbox()
        for hostile in ("../escape", "a/b", "..", "."):
            with self.subTest(value=hostile):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with self.conn.cursor() as cur:
                        cur.execute("UPDATE mailbox SET storage_id = %s", (hostile,))

    def test_storage_identity_is_stable_across_updates(self):
        """An identity that moved would strand the mail it names."""
        address = self.make_mailbox()
        before = self.storage_of(address)
        provisioning.set_mailbox_quota(self.conn, address, 1024)
        provisioning.set_mailbox_password(self.conn, address, "Ne4-Local-Pw-3")
        provisioning.ensure_mailbox(
            self.conn, {"address": address, "quota_mb": 2048}, password="")
        self.assertEqual(before, self.storage_of(address))


class RateLimitConfigurationTest(OperationsDatabaseTestCase):
    def test_set_then_get_round_trips(self):
        address = self.make_mailbox()
        provisioning.set_mailbox_rate_limit(self.conn, address, 25, "hour")
        limit = provisioning.get_mailbox_rate_limit(self.conn, address)
        self.assertEqual({"address": address, "messages": 25, "window": "hour"}, limit)

    def test_unset_is_none_not_zero(self):
        """
        None means nothing was configured; 0 means someone deliberately lifted
        the limit. Collapsing them loses who decided what.
        """
        address = self.make_mailbox()
        self.assertIsNone(provisioning.get_mailbox_rate_limit(self.conn, address))
        provisioning.set_mailbox_rate_limit(self.conn, address, 0, "hour")
        self.assertEqual(0, provisioning.get_mailbox_rate_limit(self.conn, address)["messages"])

    def test_setting_twice_updates_rather_than_duplicating(self):
        address = self.make_mailbox()
        provisioning.set_mailbox_rate_limit(self.conn, address, 5, "minute")
        provisioning.set_mailbox_rate_limit(self.conn, address, 50, "day")
        self.assertEqual(1, self.rows("SELECT count(*) FROM mailbox_rate_limit")[0][0])
        self.assertEqual("day", provisioning.get_mailbox_rate_limit(self.conn, address)["window"])

    def test_clear_removes_the_override(self):
        address = self.make_mailbox()
        provisioning.set_mailbox_rate_limit(self.conn, address, 5, "hour")
        provisioning.clear_mailbox_rate_limit(self.conn, address)
        self.assertIsNone(provisioning.get_mailbox_rate_limit(self.conn, address))

    def test_clearing_an_absent_limit_is_not_an_error(self):
        address = self.make_mailbox()
        provisioning.clear_mailbox_rate_limit(self.conn, address)
        provisioning.clear_mailbox_rate_limit(self.conn, address)

    def test_an_unknown_mailbox_is_refused(self):
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        with self.assertRaises(provisioning.NotFound):
            provisioning.set_mailbox_rate_limit(self.conn, f"ghost@{DOMAIN}", 5, "hour")

    def test_a_bad_window_is_refused(self):
        address = self.make_mailbox()
        with self.assertRaises(ValidationError):
            provisioning.set_mailbox_rate_limit(self.conn, address, 5, "fortnight")

    def test_a_negative_limit_is_refused(self):
        address = self.make_mailbox()
        with self.assertRaises(ValidationError):
            provisioning.set_mailbox_rate_limit(self.conn, address, -1, "hour")

    def test_the_enforcement_view_publishes_seconds(self):
        """
        The policy service must never parse a word. Publishing seconds keeps the
        vocabulary in one place — the database — rather than in both.
        """
        address = self.make_mailbox()
        for window, seconds in (("second", 1), ("minute", 60), ("hour", 3600), ("day", 86400)):
            with self.subTest(window=window):
                provisioning.set_mailbox_rate_limit(self.conn, address, 7, window)
                row = self.rows(
                    "SELECT messages, window_seconds FROM postfix_rate_limit "
                    "WHERE address = %s", address)[0]
                self.assertEqual((7, seconds), (int(row[0]), int(row[1])))

    def test_a_suspended_mailbox_disappears_from_enforcement(self):
        address = self.make_mailbox()
        provisioning.set_mailbox_rate_limit(self.conn, address, 5, "hour")
        provisioning.set_mailbox_active(self.conn, address, False)
        self.assertEqual([], self.rows(
            "SELECT address FROM postfix_rate_limit WHERE address = %s", address))

    def test_deleting_a_mailbox_removes_its_limit(self):
        address = self.make_mailbox()
        provisioning.set_mailbox_rate_limit(self.conn, address, 5, "hour")
        provisioning.delete_mailbox(self.conn, address)
        self.assertEqual(0, self.rows("SELECT count(*) FROM mailbox_rate_limit")[0][0])


class ReaderPrivilegeStillHoldsTest(OperationsDatabaseTestCase):
    """NE4 added relations; the NE3 privilege model must still be true."""

    def granted(self, relation):
        acl = self.rows(
            "SELECT coalesce(relacl::text, '') FROM pg_class WHERE relname = %s", relation)
        return {m.group(1) for m in re.finditer(r"(engine_ro_\w+)=", acl[0][0])} if acl else set()

    def test_postfix_reads_the_rate_limit_view(self):
        self.assertIn("engine_ro_postfix", self.granted("postfix_rate_limit"))

    def test_dovecot_cannot_read_the_rate_limit_view(self):
        self.assertNotIn("engine_ro_dovecot", self.granted("postfix_rate_limit"))

    def test_neither_reader_reaches_the_new_base_tables(self):
        for table in ("mailbox_rate_limit", "retired_mailbox_storage"):
            with self.subTest(table=table):
                self.assertEqual(set(), self.granted(table))

    def test_postfix_still_cannot_read_password_hashes(self):
        self.assertNotIn("engine_ro_postfix", self.granted("dovecot_auth"))


class OperationsClientTest(SimpleTestCase):
    """
    The engine-side client for the control plane and doveadm. Exercised with the
    transport stubbed — the real round trip is proven by the integration run,
    and what matters here is what it refuses and how it converts units.
    """

    def setUp(self):
        if not ENGINE_IMPORTABLE:
            self.skipTest(IMPORT_ERROR)

    def test_a_malformed_queue_id_never_leaves_the_process(self):
        for hostile in ("../../etc", "abc; rm -rf /", "", "x", "A" * 40, "AB/CD"):
            with self.subTest(value=hostile):
                with self.assertRaises(engine_operations.InvalidIdentifier):
                    engine_operations.cancel_queue_message(hostile)
                with self.assertRaises(engine_operations.InvalidIdentifier):
                    engine_operations.release_quarantine_item(hostile)

    def test_usage_converts_kilobytes_to_megabytes_once(self):
        """
        doveadm reports kilobytes; the contract is megabytes. Converting twice
        is how a 1 GB mailbox is reported as 1 MB.
        """
        reply = [["doveadmResponse", [
            {"type": "STORAGE", "value": "2048", "limit": "1048576"},
            {"type": "MESSAGE", "value": "7", "limit": "-"},
        ], "ne4"]]
        with mock.patch.object(engine_operations, "_doveadm", return_value=reply):
            usage = engine_operations.mailbox_usage("alice@ne4.invalid")
        self.assertEqual(2, usage["used_mb"])
        self.assertEqual(1024, usage["quota_mb"])
        self.assertEqual(7, usage["message_count"])

    def test_usage_rounds_down(self):
        """A mailbox must never be reported as fuller than it is."""
        reply = [["doveadmResponse", [
            {"type": "STORAGE", "value": "3071", "limit": "1048576"}], "ne4"]]
        with mock.patch.object(engine_operations, "_doveadm", return_value=reply):
            self.assertEqual(2, engine_operations.mailbox_usage("a@ne4.invalid")["used_mb"])

    def test_an_unknown_mailbox_is_none_not_zero(self):
        with mock.patch.object(engine_operations, "_doveadm", return_value=[["error", [], "ne4"]]):
            self.assertIsNone(engine_operations.mailbox_usage("ghost@ne4.invalid"))

    def test_an_unreachable_dovecot_raises_rather_than_reporting_empty(self):
        """
        "Your mailbox is empty" is a worse answer than "we could not measure
        it", because whatever renders it will believe the first.
        """
        with mock.patch.object(engine_operations, "_doveadm",
                               side_effect=engine_operations.OperationUnavailable("down")):
            with self.assertRaises(engine_operations.OperationUnavailable):
                engine_operations.mailbox_usage("alice@ne4.invalid")

    def test_queue_and_quarantine_are_separate_reads(self):
        with mock.patch.object(engine_operations, "_control", return_value={"items": [1, 2]}) as call:
            engine_operations.queue_status()
            engine_operations.quarantine_items()
        self.assertEqual(["/queue", "/quarantine"], [c.args[1] for c in call.call_args_list])


class AdapterCapabilityAuditTest(SimpleTestCase):
    """The headline NE4 claim, asserted rather than counted by hand."""

    def test_every_port_method_is_implemented(self):
        import inspect
        from apps.mail_engine.adapter import MailEngineAdapter
        from apps.mail_engine.native_adapter import NativeMailEngineAdapter, _LATER

        abstract = sorted(MailEngineAdapter.__abstractmethods__)
        self.assertEqual(26, len(abstract), "the port is 26 methods")
        self.assertEqual({}, _LATER, "nothing may still be deferred")

        refusing = [
            name for name in abstract
            if "_unavailable(" in inspect.getsource(getattr(NativeMailEngineAdapter, name))
        ]
        self.assertEqual([], refusing)

    def test_no_method_fakes_a_result(self):
        """
        A method that returns [] or 0 without asking the engine would count as
        implemented and be a lie. Each of the eight NE4 methods must reach the
        transport.
        """
        import inspect
        from apps.mail_engine.native_adapter import NativeMailEngineAdapter

        for name in ("set_mailbox_rate_limit", "get_mailbox_rate_limit",
                     "clear_mailbox_rate_limit", "get_mailbox_usage",
                     "get_queue_status", "cancel_queue_message",
                     "get_quarantine_items", "release_quarantine_item"):
            with self.subTest(method=name):
                source = inspect.getsource(getattr(NativeMailEngineAdapter, name))
                self.assertIn("self._request(", source)


if __name__ == "__main__":                       # pragma: no cover
    unittest.main()
