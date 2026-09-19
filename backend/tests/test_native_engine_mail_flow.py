"""
Native Engine mail flow (NE3).

WHAT THIS COVERS
    The seam where provisioning state becomes mail behaviour: the PostgreSQL
    views Postfix and Dovecot read, the grants that stop each of them reading
    the other's data, and the configuration that decides whether a message is
    signed, scanned, delivered or refused.

WHY THESE TESTS EXIST IN THIS SHAPE
    Two defects found during NE3 were invisible from the application side and
    would have shipped looking healthy:

      1. NE1's bootstrap carried `ALTER DEFAULT PRIVILEGES ... GRANT SELECT ON
         TABLES`, which covers VIEWS. Every view created afterwards was
         automatically readable by BOTH reader roles, so `engine_ro_postfix`
         could read every password hash in `dovecot_auth`. Nothing in the
         application would ever notice.

      2. Rspamd ships CLAM_VIRUS with a score of ZERO. ClamAV correctly
         identified an EICAR probe, the message scored 11.40/15.00, and it was
         DELIVERED. Antivirus that detects and does not block is worse than
         none, because a dashboard shows it working.

    Both are tested here against the real artefacts — a real PostgreSQL running
    the real migrations, and the committed configuration files — because both
    were provable only there.

EVERY DOMAIN HERE IS .invalid
    Reserved by RFC 2606, unresolvable by construction.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.parse

from django.test import SimpleTestCase

ENGINE_DIR = pathlib.Path(__file__).resolve().parents[2] / "engine" / "native_api"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

REPO = pathlib.Path(__file__).resolve().parents[2]
NE = REPO / "deploy" / "native-engine"

def _load_dkimpy():
    """
    Load the dkimpy VERIFIER, not the engine's own `dkim.py`.

    ENGINE_DIR sits at sys.path[0] and contains a module called `dkim` — the
    engine's key-store code. A plain `import dkim` here silently returns that
    instead of the verifier library, and the round-trip tests below would then
    be checking the engine against itself. Caught while writing them.

    So the engine directory is lifted off sys.path for the duration of this one
    import, and the resulting module is kept out of sys.modules afterwards so it
    cannot shadow `import dkim as engine_dkim`.
    """
    import importlib

    engine_path = str(ENGINE_DIR)
    on_path = engine_path in sys.path
    if on_path:
        sys.path.remove(engine_path)
    cached = sys.modules.pop("dkim", None)
    try:
        module = importlib.import_module("dkim")
        sys.modules.pop("dkim", None)
        return module
    finally:
        if on_path:
            sys.path.insert(0, engine_path)
        if cached is not None:
            sys.modules["dkim"] = cached


try:
    dkimpy = _load_dkimpy()
    # `sign` is what separates the verifier library from anything else that
    # happens to be importable under this name.
    DKIM_AVAILABLE = hasattr(dkimpy, "sign") and hasattr(dkimpy, "verify")
except Exception:                                           # pragma: no cover
    dkimpy = None
    DKIM_AVAILABLE = False

try:
    import psycopg
    import db as engine_db
    import dkim as engine_dkim
    import provisioning
    ENGINE_IMPORTABLE = True
    IMPORT_ERROR = ""
except Exception as exc:                                    # pragma: no cover
    ENGINE_IMPORTABLE = False
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

DOMAIN = "ne3.invalid"
OTHER_DOMAIN = "ne3-other.invalid"


def directives_only(text: str) -> str:
    """Strip comment lines so a comment can never satisfy an assertion."""
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith(("#", "--"))
    )


def service_overrides(name: str) -> str:
    """
    Everything indented under a master.cf service entry — not only the lines
    that begin with `-o`.

    Postfix's braces form spreads a single setting over several lines, so a
    parser looking for `-o` alone would miss exactly the restriction list that
    matters most here. `inet` disambiguates `smtp inet`, the port 25 listener,
    from `smtp unix`, the outbound client, which share a name.

    An empty result is meaningful rather than an error: `smtp inet` carries no
    overrides deliberately, and a test below asserts precisely that.
    """
    lines = (NE / "postfix" / "master.cf").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith(name) and "inet" in line)
    out = []
    for line in lines[start + 1:]:
        if line[:1] not in (" ", "\t"):
            break
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return "\n".join(out)


def pinned_engine_address(service: str) -> str:
    """
    The fixed address the compose file gives a service on the engine network.

    Parsed by hand because PyYAML is not a backend dependency. The point of the
    test that uses this is to compare what two files say, so restating the
    address in a third place would defeat it.
    """
    text = (NE / "docker-compose.yml").read_text(encoding="utf-8")
    block = re.search(rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  \S|\Z)", text)
    if block is None:
        raise AssertionError(f"docker-compose.yml declares no {service!r} service")
    found = re.search(r"(?m)^\s*ipv4_address:\s*(\S+)", block.group(1))
    if found is None:
        raise AssertionError(f"{service} has no pinned ipv4_address")
    return found.group(1)


# ─────────────────────────────────────────────────────────────────────────────
# Configuration: what the committed files actually say
# ─────────────────────────────────────────────────────────────────────────────


class DkimSelectorIsNotHardcodedTest(SimpleTestCase):
    """
    A per-domain selector, resolved from a map the engine publishes.

    A single hardcoded selector is wrong for every domain that has rotated onto
    a different one, and it fails QUIETLY: Rspamd looks for a key file that does
    not exist and either sends unsigned or signs with a key the published DNS
    record does not name. A signature that fails to verify is worse than no
    signature, because it reads as a forgery.
    """

    def setUp(self):
        self.raw = (NE / "rspamd" / "local.d" / "dkim_signing.conf").read_text(encoding="utf-8")
        self.directives = directives_only(self.raw)

    def test_no_literal_selector_directive(self):
        self.assertNotRegex(
            self.directives, r"(?m)^\s*selector\s*=",
            "a fixed selector signs every domain with one key name",
        )

    def test_selector_comes_from_a_map(self):
        self.assertRegex(self.directives, r"(?m)^\s*selector_map\s*=")

    def test_the_key_path_is_parameterised_by_both(self):
        self.assertRegex(self.directives, r"path\s*=.*\$domain.*\$selector")

    def test_signing_is_limited_to_authenticated_submission(self):
        """Inbound Internet mail must never leave here carrying our signature."""
        self.assertRegex(self.directives, r"sign_authenticated\s*=\s*true")

    def test_the_map_name_matches_what_the_engine_publishes(self):
        """
        The config and the writer have to agree on one filename. They are in
        different languages in different directories, so nothing but a test
        connects them.
        """
        self.assertIn(engine_dkim.SELECTOR_MAP_NAME if ENGINE_IMPORTABLE else "selectors.map",
                      self.directives)


class AntivirusActuallyBlocksTest(SimpleTestCase):
    """
    REGRESSION: ClamAV detected EICAR and the message was delivered anyway.

    Rspamd's default score for CLAM_VIRUS is 0.00. Measured through the full
    NE3 path before this file existed:

        CLAM_VIRUS(0.00){Eicar-Test-Signature;} ... [11.40/15.00] add header

    and the probe landed in the recipient's Maildir. After scoring it the same
    message produced [2011.40/15.00] reject and `554 5.7.1`.
    """

    def setUp(self):
        path = NE / "rspamd" / "local.d" / "groups.conf"
        self.assertTrue(path.is_file(), "groups.conf carries the scores that decide outcomes")
        self.directives = directives_only(path.read_text(encoding="utf-8"))

    def _score_of(self, symbol: str) -> float:
        match = re.search(
            rf'"{symbol}"\s*\{{[^}}]*?score\s*=\s*(-?[0-9.]+)', self.directives, re.S
        )
        self.assertIsNotNone(match, f"{symbol} must carry an explicit score")
        return float(match.group(1))

    def test_a_confirmed_virus_scores_above_any_reject_threshold(self):
        # Rspamd's default reject threshold is 15. A confirmed virus must decide
        # the message on its own rather than needing other symbols to help.
        self.assertGreater(self._score_of("CLAM_VIRUS"), 15.0)

    def test_a_scanner_failure_does_not_reject_mail(self):
        """
        A broken scanner is not the same as dirty mail. Postfix already defers
        when Rspamd itself is unreachable (`milter_default_action = tempfail`);
        this symbol must not additionally bounce mail because ClamAV is unwell.
        """
        self.assertLessEqual(self._score_of("CLAM_VIRUS_FAIL"), 0.0)

    def test_macro_findings_are_suspicion_not_proof(self):
        """Legitimate business documents carry macros; these inform, not decide."""
        self.assertLess(self._score_of("OLETOOLS_MACRO"), 15.0)
        self.assertLess(self._score_of("OLETOOLS_AUTOEXEC"), 15.0)


class PostfixReadsTheEngineThroughViewsTest(SimpleTestCase):
    def setUp(self):
        self.main_cf = directives_only((NE / "postfix" / "main.cf").read_text(encoding="utf-8"))
        self.entrypoint = (NE / "images" / "postfix" / "entrypoint.sh").read_text(encoding="utf-8")

    def test_every_lookup_is_a_pgsql_map(self):
        for setting in ("virtual_mailbox_domains", "virtual_mailbox_maps",
                        "virtual_alias_maps", "smtpd_sender_login_maps"):
            with self.subTest(setting=setting):
                self.assertRegex(self.main_cf, rf"(?m)^{setting}\s*=\s*pgsql:")

    def test_the_queries_select_from_views_never_base_tables(self):
        """
        A base table here would hand Postfix columns migration 003 deliberately
        withholds from it, and would break on the next schema change.
        """
        queries = re.findall(r'"(SELECT .*?)"', self.entrypoint, re.S)
        self.assertGreaterEqual(len(queries), 4, "all four maps should be rendered here")
        for query in queries:
            with self.subTest(query=query[:60]):
                self.assertRegex(query, r"FROM\s+(postfix_\w+)")
                for table in (" mailbox", " domain", " alias", " dkim_key", " forwarding"):
                    self.assertNotIn(f"FROM{table}", query)

    def test_sender_authorisation_is_enforced(self):
        """
        Without this an authenticated customer could put any address in MAIL
        FROM — including another tenant's — and we would DKIM-sign it.

        NE7 moved the two restrictions out of main.cf and onto the submission
        service. That move is the fix, not a regression. Global in main.cf they
        also applied to port 25, where no SASL layer exists, and Postfix
        answered by logging

            warning: restriction `reject_sender_login_mismatch' ignored:
            no SASL support

        and enforcing nothing. A silently discarded check reads exactly like a
        check that passes, which is why this assertion now follows the
        restrictions to the service that actually authenticates.

        Who may use which address is `SenderAuthorisationViewTest` below; this
        is whether Postfix consults it at all.
        """
        submission = service_overrides("submission")

        self.assertIn("reject_sender_login_mismatch", submission)
        self.assertIn("reject_authenticated_sender_login_mismatch", submission)

        # Both are inert without this line — the omission that left them
        # ignored on port 25 in the first place.
        self.assertRegex(submission, r"smtpd_sasl_auth_enable\s*=\s*yes")

        # The map they consult is data rather than policy, so it stays global.
        self.assertRegex(self.main_cf, r"(?m)^smtpd_sender_login_maps\s*=\s*pgsql:")

    def test_port_25_does_not_rest_on_a_check_it_cannot_run(self):
        """
        The companion to the test above, and the reason the restrictions moved.

        Port 25 accepts mail from strangers and offers no SASL, so a
        sender-login restriction there can never be enforced. Returning one to
        main.cf would restore both the warning and the false impression of a
        check. Inbound senders are constrained by the policy service and the
        recipient maps instead.
        """
        self.assertNotIn("sender_login_mismatch", service_overrides("smtp"))
        self.assertNotIn("sender_login_mismatch", self.main_cf)

    def test_delivery_goes_to_dovecot_over_lmtp(self):
        """
        Postfix must not write the mail store; one component owns Maildir.

        The destination address is compared against the compose file rather
        than written out here, so this cannot pass a pair of files that have
        drifted apart — and the address itself stays defined in one place.
        """
        transport = re.search(r"(?m)^virtual_transport\s*=\s*(.+)$", self.main_cf)
        self.assertIsNotNone(transport, "virtual_transport must be set")
        destination = transport.group(1).strip()

        self.assertTrue(
            destination.startswith("lmtp:inet:"),
            f"delivery must hand off over LMTP, not {destination!r}",
        )

        # Postfix writing the store itself would need this. Its absence is what
        # keeps Maildir owned by exactly one process.
        self.assertNotRegex(self.main_cf, r"(?m)^virtual_mailbox_base\s*=\s*\S")

        pinned = re.fullmatch(r"lmtp:inet:(\d+\.\d+\.\d+\.\d+):(\d+)", destination)
        self.assertIsNotNone(pinned, (
            "NE7 regression: the destination must be a pinned ADDRESS, never a "
            "name. Docker's embedded DNS answers NXDOMAIN for a stopped "
            "container and Postfix treats a host that does not exist as a "
            "PERMANENT failure (dsn=5.4.4), so a routine Dovecot restart "
            "returned inbound customer mail to its senders as a hard bounce. "
            "A refused connection to an address is temporary (dsn=4.4.1) and "
            f"the message waits in the queue instead. Found: {destination!r}"
        ))

        self.assertEqual(
            pinned_engine_address("dovecot"), pinned.group(1),
            "Postfix delivers to an address the compose file does not give "
            "Dovecot, so mail would go nowhere",
        )
        self.assertEqual("24", pinned.group(2), "Dovecot's LMTP listener is 24")

    def test_mynetworks_never_covers_the_container_subnet(self):
        """
        `permit_mynetworks` comes first in the relay restrictions. That is safe
        only while mynetworks is host-local; widening it to the compose subnet
        would make every container on that network an open relay.
        """
        self.assertIn("mynetworks_style = host", self.main_cf)
        self.assertNotIn("172.27.0.0/16", self.main_cf)

    def test_the_filter_fails_closed(self):
        """
        A message that skipped the filter is worse than one that arrives late,
        so an unavailable Rspamd must defer rather than pass mail through.
        """
        self.assertRegex(self.main_cf, r"milter_default_action\s*=\s*tempfail")

    def test_no_database_password_is_committed(self):
        self.assertNotRegex(
            self.main_cf, r"(?m)^\s*password\s*=",
            "credentials belong in the root-only .env, not in Git",
        )


class SecretsAreRenderedNotCommittedTest(SimpleTestCase):
    """
    Both entrypoints exist because neither daemon can read a secret from the
    environment itself. Dovecot's `%{env:...}` PARSES and then reaches libpq as
    an empty string — "fe_sendauth: no password supplied" — which is the worst
    kind of failure: the config looks right and nobody can log in.
    """

    def setUp(self):
        self.dovecot = (NE / "images" / "dovecot" / "entrypoint.sh").read_text(encoding="utf-8")
        self.postfix = (NE / "images" / "postfix" / "entrypoint.sh").read_text(encoding="utf-8")

    def test_each_refuses_to_start_without_its_credential(self):
        """
        Starting without a password would mean a healthy container that
        authenticates nobody, or one that defers all mail — both read as
        something other than a missing environment variable for days.
        """
        for name, script, var in (
            ("dovecot", self.dovecot, "NATIVE_DOVECOT_DB_PASSWORD"),
            ("postfix", self.postfix, "NATIVE_POSTFIX_DB_PASSWORD"),
        ):
            with self.subTest(image=name):
                self.assertIn(var, script)
                self.assertRegex(script, r"exit\s+78")

    def test_each_rejects_a_password_it_cannot_represent(self):
        """
        A '#' opens a comment and a newline ends a setting in BOTH config
        formats, so an unlucky password would silently truncate the credential
        or terminate the block. The alphabet is constrained instead.
        """
        for name, script in (("dovecot", self.dovecot), ("postfix", self.postfix)):
            with self.subTest(image=name):
                self.assertRegex(script, r"\*\[!A-Za-z0-9[^]]*\]\*")

    def test_no_secret_is_committed_in_the_dovecot_config(self):
        conf = directives_only((NE / "dovecot" / "dovecot.conf").read_text(encoding="utf-8"))
        self.assertNotRegex(
            conf, r"(?m)^\s*password\s*=\s*\S",
            "the connection is included from a file generated at container start",
        )
        self.assertIn("!include engine-db.conf", conf)


class DovecotStaysReadOnlyTest(SimpleTestCase):
    def setUp(self):
        self.conf = directives_only((NE / "dovecot" / "dovecot.conf").read_text(encoding="utf-8"))

    def test_last_login_is_reported_not_written(self):
        """
        Dovecot's own last_login plugin writes to a dictionary, which would mean
        giving Dovecot write access to the engine database. The auth-policy hook
        reports to the API instead, so the API stays the only writer.
        """
        self.assertNotRegex(self.conf, r"mail_plugins[^}]*last_login")
        self.assertIn("!include engine-policy.conf", self.conf)

    def test_login_never_depends_on_the_reporting_endpoint(self):
        """
        Bookkeeping must not be able to lock customers out of their mail, so the
        hook reports AFTER the fact and never asks permission.
        """
        entry = (NE / "images" / "dovecot" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("auth_policy_check_before_auth = no", entry)
        self.assertIn("auth_policy_reject_on_fail = no", entry)
        self.assertIn("auth_policy_report_after_auth = yes", entry)

    def test_the_policy_credential_is_separate_from_the_provisioning_secret(self):
        """
        Dovecot is the most network-exposed component in the engine. Handing it
        the provisioning secret so it can record a login would let a compromised
        Dovecot create domains, mint mailboxes and rotate DKIM keys.
        """
        app = (REPO / "engine" / "native_api" / "app.py").read_text(encoding="utf-8")
        self.assertIn("NATIVE_DOVECOT_POLICY_SECRET", app)
        self.assertIn("X-Native-Policy-Secret", app)
        self.assertRegex(app, r"def policy_authorized")
        # The policy route must be checked before, and separately from, the
        # provisioning authorisation.
        self.assertRegex(app, r"policy_authorized\(self\)")


# ─────────────────────────────────────────────────────────────────────────────
# The database: grants and view semantics, against a real PostgreSQL
# ─────────────────────────────────────────────────────────────────────────────


def _admin_dsn() -> str | None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    parts = urllib.parse.urlparse(url)
    if not parts.hostname:
        return None
    return (
        f"host={parts.hostname} port={parts.port or 5432} "
        f"dbname=postgres user={parts.username or ''} "
        f"password={urllib.parse.unquote(parts.password or '')}"
    )


class MailFlowDatabaseTestCase(SimpleTestCase):
    """A scratch engine database running the real migrations, bootstrap included."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not ENGINE_IMPORTABLE:
            raise unittest.SkipTest(f"engine modules not importable — {IMPORT_ERROR}")
        admin = _admin_dsn()
        if admin is None:
            raise unittest.SkipTest("DATABASE_URL is not set; no PostgreSQL to test against")

        cls.db_name = f"ne3_flow_test_{os.getpid()}"
        try:
            with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{cls.db_name}"')
                cur.execute(f'CREATE DATABASE "{cls.db_name}"')
        except psycopg.Error as exc:
            raise unittest.SkipTest(f"cannot create a scratch engine database: {exc}")

        cls._admin = admin
        cls.engine_dsn = admin.replace("dbname=postgres", f"dbname={cls.db_name}")

        cls.key_dir = pathlib.Path(tempfile.mkdtemp(prefix="ne3-dkim-"))
        cls._real_key_dir = engine_dkim.KEY_DIR
        engine_dkim.KEY_DIR = cls.key_dir

        for name in ("", ".db", ".dkim", ".provisioning"):
            logging.getLogger(f"matemail.native.api{name}").setLevel(logging.ERROR)

        cls.conn = psycopg.connect(cls.engine_dsn, autocommit=True)

        # THE BOOTSTRAP RUNS FIRST, deliberately. It is what introduces the
        # default-privilege rule that migration 003 has to retire; applying only
        # the migrations would test a database that never had the defect.
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
                "dkim_key RESTART IDENTITY CASCADE"
            )

    # ── helpers ──
    def rows(self, sql, *args):
        with self.conn.cursor() as cur:
            cur.execute(sql, args or None)
            return cur.fetchall()

    def seed(self):
        """A domain with two mailboxes, an internal alias, an external alias
        and a forwarding rule — the shapes whose authorisation differs."""
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.ensure_mailbox(
            self.conn, {"address": f"alice@{DOMAIN}", "quota_mb": 512}, password="alice-pw-ne3")
        provisioning.ensure_mailbox(
            self.conn, {"address": f"bob@{DOMAIN}", "quota_mb": 1024}, password="bob-pw-ne3")
        provisioning.ensure_alias(
            self.conn, {"address": f"team@{DOMAIN}", "destinations": [f"alice@{DOMAIN}"]})
        provisioning.ensure_alias(
            self.conn, {"address": f"ext@{DOMAIN}", "destinations": ["outside@elsewhere.invalid"]})
        provisioning.ensure_forwarding(
            self.conn, {"mailbox_address": f"bob@{DOMAIN}",
                        "destinations": ["outside@elsewhere.invalid"]})


class ReaderPrivilegeTest(MailFlowDatabaseTestCase):
    """
    REGRESSION: `engine_ro_postfix` could read every password hash.

    NE1's bootstrap set `ALTER DEFAULT PRIVILEGES ... GRANT SELECT ON TABLES`,
    and "TABLES" includes views, so both reader roles were granted SELECT on
    every relation created afterwards — automatically, silently, and regardless
    of what the GRANT statements in the migration said.
    """

    def _granted(self, relation: str) -> set[str]:
        acl = self.rows(
            "SELECT coalesce(relacl::text, '') FROM pg_class WHERE relname = %s", relation
        )
        self.assertTrue(acl, f"{relation} should exist")
        return {
            match.group(1)
            for match in re.finditer(r"(engine_ro_\w+)=([a-zA-Z]*)", acl[0][0])
        }

    def test_postfix_cannot_read_password_hashes(self):
        """The single most damaging cross-read available in this schema."""
        self.assertNotIn("engine_ro_postfix", self._granted("dovecot_auth"))

    def test_postfix_cannot_read_the_dovecot_userdb(self):
        self.assertNotIn("engine_ro_postfix", self._granted("dovecot_userdb"))

    def test_dovecot_cannot_read_the_routing_views(self):
        for view in ("postfix_virtual_alias", "postfix_sender_login",
                     "postfix_virtual_domain", "postfix_virtual_mailbox"):
            with self.subTest(view=view):
                self.assertNotIn("engine_ro_dovecot", self._granted(view))

    def test_each_reader_still_has_what_it_needs(self):
        """Least privilege that breaks mail is not least privilege."""
        for view in ("postfix_virtual_domain", "postfix_virtual_mailbox",
                     "postfix_virtual_alias", "postfix_sender_login"):
            with self.subTest(view=view):
                self.assertIn("engine_ro_postfix", self._granted(view))
        for view in ("dovecot_auth", "dovecot_userdb"):
            with self.subTest(view=view):
                self.assertIn("engine_ro_dovecot", self._granted(view))

    def test_neither_reader_can_reach_a_base_table(self):
        for table in ("mailbox", "domain", "alias", "alias_destination",
                      "forwarding", "dkim_key"):
            with self.subTest(table=table):
                self.assertEqual(set(), self._granted(table))

    def test_a_new_relation_is_not_granted_to_anyone_automatically(self):
        """
        THE DEFECT ITSELF. Under the NE1 rule a freshly created view came out
        carrying `engine_ro_postfix=r , engine_ro_dovecot=r`. Any future
        migration adding a table — a rate-limit counter, a token store — would
        have been exposed the same way, and the REVOKE would have to be
        remembered every single time.
        """
        with self.conn.cursor() as cur:
            cur.execute("CREATE VIEW ne3_canary AS SELECT 1 AS x")
        try:
            self.assertEqual(
                set(), self._granted("ne3_canary"),
                "a new relation must start readable by nobody",
            )
        finally:
            with self.conn.cursor() as cur:
                cur.execute("DROP VIEW ne3_canary")

    def test_the_readers_can_login_but_hold_no_power(self):
        for role in ("engine_ro_postfix", "engine_ro_dovecot"):
            with self.subTest(role=role):
                row = self.rows(
                    "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication "
                    "FROM pg_roles WHERE rolname = %s", role
                )[0]
                self.assertTrue(row[0], "NE3 is when these roles actually connect")
                self.assertEqual([False] * 4, list(row[1:]))


class SenderAuthorisationViewTest(MailFlowDatabaseTestCase):
    """
    `postfix_sender_login` is the map behind
    `reject_authenticated_sender_login_mismatch`. What it returns is exactly
    who may put an address in MAIL FROM.
    """

    def owners_of(self, address: str) -> set[str]:
        return {r[0] for r in self.rows(
            "SELECT owner FROM postfix_sender_login WHERE address = %s", address)}

    def test_a_mailbox_owns_its_own_address(self):
        self.seed()
        self.assertEqual({f"alice@{DOMAIN}"}, self.owners_of(f"alice@{DOMAIN}"))

    def test_an_internal_alias_authorises_its_destination_mailbox(self):
        """
        The mailcow limitation this schema exists to fix: there an alias could
        receive mail but its owner could not send as it.
        """
        self.seed()
        self.assertEqual({f"alice@{DOMAIN}"}, self.owners_of(f"team@{DOMAIN}"))

    def test_an_external_alias_destination_authorises_nobody(self):
        """
        An alias pointing outside has a NULL mailbox_id and is excluded by the
        INNER JOIN — structurally, not by a WHERE clause someone maintains.
        """
        self.seed()
        self.assertEqual(set(), self.owners_of(f"ext@{DOMAIN}"))

    def test_forwarding_confers_no_sending_right(self):
        """
        Forwarding says "copy my mail elsewhere". If it leaked into this view,
        pointing forwarding at an address would be enough to spoof it. The view
        does not reference the `forwarding` table at all.
        """
        self.seed()
        self.assertEqual(set(), self.owners_of("outside@elsewhere.invalid"))
        definition = self.rows(
            "SELECT pg_get_viewdef('postfix_sender_login'::regclass, true)")[0][0]
        self.assertNotIn("forwarding", definition)

    def test_forwarding_still_delivers(self):
        """The right that was withheld above must not have broken delivery."""
        self.seed()
        destinations = {r[0] for r in self.rows(
            "SELECT destination FROM postfix_virtual_alias WHERE address = %s", f"bob@{DOMAIN}")}
        self.assertIn("outside@elsewhere.invalid", destinations)

    def test_a_suspended_mailbox_may_not_send(self):
        self.seed()
        provisioning.set_mailbox_active(self.conn, f"alice@{DOMAIN}", False)
        self.assertEqual(set(), self.owners_of(f"alice@{DOMAIN}"))
        self.assertEqual(set(), self.owners_of(f"team@{DOMAIN}"),
                         "an alias owned by a suspended mailbox must go with it")

    def test_a_suspended_domain_takes_every_mailbox_with_it(self):
        self.seed()
        provisioning.set_domain_active(self.conn, DOMAIN, False)
        self.assertEqual([], self.rows("SELECT owner FROM postfix_sender_login"))
        self.assertEqual([], self.rows("SELECT name FROM postfix_virtual_domain"))


class DovecotViewSemanticsTest(MailFlowDatabaseTestCase):
    def test_a_mailbox_with_no_password_cannot_authenticate(self):
        """
        A row with no credential must read as "unknown user", not as "user with
        no password" — the second invites a passwordless login path.
        """
        self.seed()
        with self.conn.cursor() as cur:
            cur.execute("UPDATE mailbox SET password_hash = NULL WHERE address = %s",
                        (f"bob@{DOMAIN}",))
        self.assertEqual([], self.rows(
            "SELECT address FROM dovecot_auth WHERE address = %s", f"bob@{DOMAIN}"))

    def test_a_mailbox_with_no_password_still_receives_mail(self):
        """A mailbox mid-provisioning must not bounce mail addressed to it."""
        self.seed()
        with self.conn.cursor() as cur:
            cur.execute("UPDATE mailbox SET password_hash = NULL WHERE address = %s",
                        (f"bob@{DOMAIN}",))
        self.assertEqual(1, len(self.rows(
            "SELECT address FROM dovecot_userdb WHERE address = %s", f"bob@{DOMAIN}")))

    def test_only_a_hashed_password_is_ever_published(self):
        self.seed()
        value = self.rows(
            "SELECT password_hash FROM dovecot_auth WHERE address = %s", f"alice@{DOMAIN}")[0][0]
        self.assertRegex(value, r"^\{[A-Z0-9-]+\}")
        self.assertNotIn("alice-pw-ne3", value)

    def test_the_quota_carries_its_unit(self):
        """
        Dovecot reads a bare number as BYTES. A 1024 MB mailbox published as
        "1024" would become a 1 KB mailbox, and every customer would be over
        quota at once.
        """
        self.seed()
        value = self.rows(
            "SELECT quota_storage_size FROM dovecot_userdb WHERE address = %s",
            f"bob@{DOMAIN}")[0][0]
        self.assertEqual("1024M", value)

    def test_the_userdb_publishes_dovecot_24_setting_names(self):
        """
        2.4 turned userdb fields into settings: `home`, `uid` and `gid` no
        longer exist and a query returning them fails the lookup outright.
        """
        columns = {r[0] for r in self.rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'dovecot_userdb'")}
        self.assertIn("mail_home", columns)
        self.assertIn("quota_storage_size", columns)
        for stale in ("home", "uid", "gid"):
            with self.subTest(column=stale):
                self.assertNotIn(stale, columns)

    def test_uid_and_gid_are_not_served_from_the_database(self):
        """
        Every mailbox is delivered as 5000:5000. Serving that from the database
        would let a database compromise choose the uid that writes to disk; it
        is pinned in dovecot.conf instead.
        """
        conf = directives_only((NE / "dovecot" / "dovecot.conf").read_text(encoding="utf-8"))
        self.assertRegex(conf, r"(?m)^mail_uid\s*=\s*5000")
        self.assertRegex(conf, r"(?m)^mail_gid\s*=\s*5000")
        self.assertRegex(conf, r"(?m)^first_valid_uid\s*=\s*5000")

    def test_suspension_removes_a_mailbox_from_both_views(self):
        self.seed()
        provisioning.set_mailbox_active(self.conn, f"alice@{DOMAIN}", False)
        for view in ("dovecot_auth", "dovecot_userdb"):
            with self.subTest(view=view):
                self.assertEqual([], self.rows(
                    f"SELECT address FROM {view} WHERE address = %s", f"alice@{DOMAIN}"))


class SelectorMapPublicationTest(MailFlowDatabaseTestCase):
    """
    The map Rspamd resolves each domain's selector from. It is part of the key
    store, so the engine API writes it and nothing else does.
    """

    def test_every_domain_with_a_key_appears(self):
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.ensure_domain(
            self.conn, {"name": OTHER_DOMAIN, "dkim_selector": "alt7"})
        published = engine_dkim.selector_map_path().read_text(encoding="ascii")
        self.assertIn(f"{DOMAIN} mm1", published)
        self.assertIn(f"{OTHER_DOMAIN} alt7", published)

    def test_a_rotation_moves_the_domain_onto_its_new_selector(self):
        """
        The case a hardcoded selector gets wrong. After rotation the map must
        name the key that will actually sign, or the signature will not match
        the DNS record the customer published.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "rot2")
        published = engine_dkim.selector_map_path().read_text(encoding="ascii")
        self.assertIn(f"{DOMAIN} rot2", published)
        self.assertNotIn(f"{DOMAIN} mm1", published)

    def test_the_named_key_file_exists(self):
        """A map entry pointing at a key that is not there signs nothing."""
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        for line in engine_dkim.selector_map_path().read_text(encoding="ascii").splitlines():
            domain, selector = line.split()
            with self.subTest(domain=domain):
                self.assertTrue(engine_dkim.key_path(domain, selector).is_file())

    def test_a_deleted_domain_leaves_the_map(self):
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.ensure_domain(self.conn, {"name": OTHER_DOMAIN})
        provisioning.delete_dkim_key(self.conn, DOMAIN)
        published = engine_dkim.selector_map_path().read_text(encoding="ascii")
        self.assertNotIn(DOMAIN + " ", published)
        self.assertIn(OTHER_DOMAIN + " ", published)

    def test_the_map_is_not_key_material(self):
        """
        It maps a domain to a selector; both are published in DNS. It is
        world-readable on purpose, and must never contain a private key.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        published = engine_dkim.selector_map_path().read_text(encoding="ascii")
        self.assertNotIn("PRIVATE KEY", published)


class DkimMaterialVerifiesSignaturesTest(MailFlowDatabaseTestCase):
    """
    The invariant that decides whether mail this engine sends is accepted: the
    public material the Native API hands a customer for their DNS record must
    verify signatures made with the private key the engine actually stored.

    If those two ever drift, every message goes out with a signature the world
    rejects and nothing inside MateMail notices — the header is present, the key
    file exists, the API answers. Only a receiver finds out.

    The end-to-end proof (a message produced by the real
    Postfix -> Rspamd -> LMTP flow, verified against the API's key, before and
    after a selector rotation) is a manual acceptance gate. This class keeps the
    property from regressing without needing the whole stack running.
    """

    def api_material(self, domain: str) -> dict:
        """Exactly what `GET /v1/dkim` returns, through the real code path."""
        material = provisioning.get_dkim_public_key(self.conn, domain)
        self.assertIsNotNone(material, "the domain should have DKIM material")
        return material

    def test_the_api_public_key_is_the_counterpart_of_the_stored_private_key(self):
        """
        DERIVED, not stored-and-hoped. The API must publish the public half of
        the key that will actually sign, so a filesystem/database disagreement
        cannot produce a DNS record nobody can verify against.
        """
        import base64
        from cryptography.hazmat.primitives import serialization

        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        material = self.api_material(DOMAIN)

        private_path = engine_dkim.key_path(DOMAIN, material["selector"])
        self.assertTrue(private_path.is_file(), "the signing key must exist")
        private_key = serialization.load_pem_private_key(
            private_path.read_bytes(), password=None)
        der = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        self.assertEqual(base64.b64encode(der).decode(), material["public_key"])

    def test_the_selector_agrees_everywhere_it_is_written(self):
        """
        Three places name the selector: the API response, the map Rspamd reads,
        and the key filename Rspamd opens. A disagreement between any two means
        signing with a key the published record does not describe.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        material = self.api_material(DOMAIN)
        selector = material["selector"]

        published = engine_dkim.selector_map_path().read_text(encoding="ascii")
        self.assertIn(DOMAIN + " " + selector, published)
        self.assertTrue(engine_dkim.key_path(DOMAIN, selector).is_file())
        self.assertEqual(
            material["dns_record_name"], selector + "._domainkey." + DOMAIN)

    def test_a_stale_database_row_does_not_change_what_the_api_publishes(self):
        """
        The KEY FILE is the source of truth, not the row (DEC-007r).

        A crash between writing the key and updating the row leaves a stale
        `public_key` in the database. Publishing that would hand the customer a
        DNS record describing a key that no longer signs anything, and every
        message the engine sent would fail verification at the receiver while
        looking perfectly healthy here.

        This test creates the drift deliberately, because in a healthy database
        the row and the file agree and the distinction is invisible — a mutation
        that swapped one for the other passed every other test in this file.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        truth = self.api_material(DOMAIN)["public_key"]

        # Corrupt the row only. The key on disk is untouched.
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE dkim_key SET public_key = %s WHERE domain_name = %s",
                ("AAAA" + truth[4:], DOMAIN),
            )

        republished = self.api_material(DOMAIN)
        self.assertEqual(
            truth, republished["public_key"],
            "the API must publish the key derived from the live private key, "
            "never a stale row",
        )
        self.assertIn(truth, republished["dns_record_value"])

    @unittest.skipUnless(DKIM_AVAILABLE, "dkimpy is not installed")
    def test_a_stale_row_still_yields_material_that_verifies(self):
        """
        The same drift, carried to the outcome that matters: after the row has
        gone stale, a signature made with the stored key must still verify
        against what the API publishes.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        material = self.api_material(DOMAIN)
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE dkim_key SET public_key = %s WHERE domain_name = %s",
                ("AAAA" + material["public_key"][4:], DOMAIN),
            )

        republished = self.api_material(DOMAIN)
        signed = self._sign(DOMAIN, republished["selector"])
        self.assertTrue(
            dkimpy.verify(signed, dnsfunc=self._served(
                republished["dns_record_name"], republished["dns_record_value"])),
            "a stale row must not be able to publish unverifiable material",
        )

    # ── the cryptographic round trip ──

    def _sign(self, domain: str, selector: str, body: str = "NE3 regression body.") -> bytes:
        """
        Sign with the key the ENGINE stored — the same file Rspamd opens.

        This is not stand-in signing logic bolted on for coverage: the private
        key is read from the engine's own key store, so this fails exactly when
        the stored key and the published material disagree, which is the point.
        """
        crlf = chr(13) + chr(10)
        message = (
            "From: alice@" + domain + crlf +
            "To: bob@" + domain + crlf +
            "Subject: NE3 DKIM regression" + crlf +
            crlf + body + crlf
        ).encode()
        private_pem = engine_dkim.key_path(domain, selector).read_bytes()
        signature = dkimpy.sign(
            message, selector.encode(), domain.encode(), private_pem,
            include_headers=[b"from", b"to", b"subject"])
        return signature + message

    @staticmethod
    def _served(record_name: str, record_value: str):
        """A deterministic stand-in for DNS. `.invalid` cannot publish records."""
        def dnsfunc(name, timeout=5):
            key = name.decode() if isinstance(name, bytes) else name
            if key.rstrip(".") != record_name:
                raise dkimpy.KeyFormatError("no key served for " + str(key))
            return record_value.encode()
        return dnsfunc

    @unittest.skipUnless(DKIM_AVAILABLE, "dkimpy is not installed")
    def test_a_signature_verifies_against_the_api_material(self):
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        material = self.api_material(DOMAIN)
        signed = self._sign(DOMAIN, material["selector"])
        self.assertTrue(
            dkimpy.verify(signed, dnsfunc=self._served(
                material["dns_record_name"], material["dns_record_value"])),
            "mail signed by this engine must verify against the record we publish",
        )

    @unittest.skipUnless(DKIM_AVAILABLE, "dkimpy is not installed")
    def test_it_does_not_verify_against_an_unrelated_key(self):
        """
        Without this, the test above would pass even if the verifier said yes to
        everything, which would make the whole class worthless.
        """
        import base64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        material = self.api_material(DOMAIN)
        signed = self._sign(DOMAIN, material["selector"])

        unrelated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        der = unrelated.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        wrong_record = "v=DKIM1; k=rsa; p=" + base64.b64encode(der).decode()

        self.assertFalse(
            dkimpy.verify(signed, dnsfunc=self._served(
                material["dns_record_name"], wrong_record)),
        )

    @unittest.skipUnless(DKIM_AVAILABLE, "dkimpy is not installed")
    def test_rotation_moves_verification_onto_the_new_key(self):
        """
        The case a hardcoded selector gets wrong, carried all the way to the
        cryptography: after rotation, mail must verify against the NEW published
        record and must not verify against the previous one.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        before = self.api_material(DOMAIN)
        old_signed = self._sign(DOMAIN, before["selector"])

        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")
        after = self.api_material(DOMAIN)
        self.assertEqual("alt7", after["selector"])
        new_signed = self._sign(DOMAIN, "alt7")

        # New mail verifies against the new record.
        self.assertTrue(dkimpy.verify(new_signed, dnsfunc=self._served(
            after["dns_record_name"], after["dns_record_value"])))
        # And not against the previous material served under the new name.
        self.assertFalse(dkimpy.verify(new_signed, dnsfunc=self._served(
            after["dns_record_name"], before["dns_record_value"])))
        # Mail signed before the rotation still verifies with the old record —
        # which is why the old DNS record has to outlive the rotation.
        self.assertTrue(dkimpy.verify(old_signed, dnsfunc=self._served(
            before["dns_record_name"], before["dns_record_value"])))


class DkimRetirementTest(MailFlowDatabaseTestCase):
    """
    A selector change must never create an unsigned interval.

    Rspamd resolves the selector from `selectors.map`, which it re-reads on its
    own schedule. Deleting the old key the instant the map changed left a window
    where a worker still holding the previous map opened a key that no longer
    existed and sent the message UNSIGNED. Observed propagation was fast — around
    13 seconds — but "usually fast" is a probability, not an invariant.

    So a selector change RETIRES the outgoing key: the file keeps its exact name,
    because that is the name a stale worker opens, and a sidecar marker records
    when retirement happened so reconciliation can remove it later.

    During the grace period both outcomes are correct signatures, and neither is
    unsigned:

        stale worker -> old selector + old key -> verifies against the old record
        fresh worker -> new selector + new key -> verifies against the new record
    """

    def marker(self, selector: str):
        return engine_dkim.retirement_marker_path(DOMAIN, selector)

    def test_a_selector_change_retires_the_old_key_instead_of_deleting_it(self):
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")

        self.assertTrue(
            engine_dkim.key_path(DOMAIN, "mm1").is_file(),
            "the outgoing key must survive: a stale Rspamd worker opens it by name",
        )
        self.assertTrue(self.marker("mm1").is_file(), "and it must be marked retired")
        self.assertIsNotNone(engine_dkim.retired_at(DOMAIN, "mm1"))

    def test_the_new_selector_is_the_only_authoritative_one(self):
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")

        material = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        self.assertEqual("alt7", material["selector"])
        self.assertIn("alt7", material["dns_record_name"])
        # The retired selector must not leak into anything a customer is told to
        # publish, or they would create a DNS record for a key about to expire.
        self.assertNotIn("mm1", material["dns_record_name"])
        self.assertNotIn("mm1", material["dns_record_value"])

        published = engine_dkim.selector_map_path().read_text(encoding="ascii")
        self.assertIn(DOMAIN + " alt7", published)
        self.assertNotIn(DOMAIN + " mm1", published)

    def test_a_retired_key_is_never_served_through_the_api(self):
        """A retired key signs for stale workers. Nobody may ask for it."""
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")

        listed = provisioning.list_domains(self.conn)
        material = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        for blob in (str(listed), str(material)):
            self.assertNotIn("PRIVATE KEY", blob)
        self.assertNotEqual("mm1", material["selector"])

    # ── the cleanup boundary ──

    def test_a_recently_retired_key_survives_reconciliation(self):
        """
        THE POINT OF THE WHOLE MECHANISM. Reconciliation used to treat any key
        the database does not name as an orphan; that rule would delete the key a
        stale worker is about to use.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")

        report = provisioning.reconcile_dkim(self.conn)
        self.assertIn(DOMAIN + ".mm1.key", report["retired_retained"])
        self.assertEqual([], report["retired_removed"])
        self.assertTrue(engine_dkim.key_path(DOMAIN, "mm1").is_file())
        self.assertNotIn(DOMAIN + ".mm1.key", report["orphans_removed"])

    def test_an_expired_retired_key_is_removed(self):
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")

        # Backdate the retirement rather than sleeping out the grace period.
        engine_dkim.retire(
            DOMAIN, "mm1",
            when=time.time() - engine_dkim.RETIREMENT_GRACE_SECONDS - 60,
        )
        report = provisioning.reconcile_dkim(self.conn)

        self.assertIn(DOMAIN + ".mm1.key", report["retired_removed"])
        self.assertFalse(engine_dkim.key_path(DOMAIN, "mm1").is_file())
        self.assertFalse(self.marker("mm1").is_file(), "the marker goes with the key")

    def test_expiry_does_not_touch_the_live_key(self):
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")
        engine_dkim.retire(
            DOMAIN, "mm1",
            when=time.time() - engine_dkim.RETIREMENT_GRACE_SECONDS - 60,
        )
        provisioning.reconcile_dkim(self.conn)

        self.assertTrue(engine_dkim.key_path(DOMAIN, "alt7").is_file())
        self.assertEqual(
            "alt7", provisioning.get_dkim_public_key(self.conn, DOMAIN)["selector"])

    def test_the_grace_comfortably_exceeds_rspamd_map_propagation(self):
        """
        Rspamd's file maps refresh at `map_watch_interval * map_file_watch_multiplier`
        — 300 * 0.1 = 30s at the shipped defaults, measured with `rspamadm
        configdump`. The grace has to cover that plus scheduling slack with room
        to spare, not merely beat the observed case.
        """
        self.assertGreaterEqual(
            engine_dkim.RETIREMENT_GRACE_SECONDS, 600,
            "a grace this short leaves the invariant resting on timing",
        )

    # ── crash and concurrency ──

    def test_a_marker_left_on_the_live_selector_is_cleared(self):
        """
        Crash window: the marker is written BEFORE the new key is activated, so a
        death in between leaves the still-authoritative selector marked retired.
        Left alone it would expire and delete the key the engine signs with.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        engine_dkim.retire(DOMAIN, "mm1")          # mm1 is still authoritative
        self.assertTrue(self.marker("mm1").is_file())

        provisioning.reconcile_dkim(self.conn)

        self.assertFalse(self.marker("mm1").is_file())
        self.assertTrue(engine_dkim.key_path(DOMAIN, "mm1").is_file())
        self.assertEqual(
            "mm1", provisioning.get_dkim_public_key(self.conn, DOMAIN)["selector"])

    def test_a_rolled_back_rotation_leaves_no_retirement(self):
        """
        A handled failure must be a clean no-op. If the retirement survived it,
        the live key would be on a countdown to deletion for a rotation that
        never happened.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        original = provisioning.get_dkim_public_key(self.conn, DOMAIN)

        with mock.patch.object(
            engine_dkim, "activate", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")

        self.assertFalse(self.marker("mm1").is_file())
        self.assertTrue(engine_dkim.key_path(DOMAIN, "mm1").is_file())
        self.assertEqual(
            original["public_key"],
            provisioning.get_dkim_public_key(self.conn, DOMAIN)["public_key"],
        )

    def test_a_marker_without_a_key_is_cleaned_up(self):
        """A marker describing nothing is litter, and must not accumulate."""
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        engine_dkim.retire(DOMAIN, "ghost")
        self.assertTrue(engine_dkim.retirement_marker_path(DOMAIN, "ghost").is_file())

        provisioning.reconcile_dkim(self.conn)

        self.assertFalse(engine_dkim.retirement_marker_path(DOMAIN, "ghost").is_file())

    def test_deleting_a_domain_removes_its_retired_material_too(self):
        """
        Once the domain has no DKIM at all there is nothing left for a retired
        key to sign, and leaving private key material behind after an explicit
        delete is the wrong default.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")
        self.assertTrue(engine_dkim.key_path(DOMAIN, "mm1").is_file())

        provisioning.delete_dkim_key(self.conn, DOMAIN)

        self.assertFalse(engine_dkim.key_path(DOMAIN, "mm1").is_file())
        self.assertFalse(self.marker("mm1").is_file())
        self.assertFalse(engine_dkim.key_path(DOMAIN, "alt7").is_file())

    def test_repeated_reconciliation_is_idempotent(self):
        """Reconciliation runs at every startup; it must not erode the grace."""
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")
        for _ in range(3):
            provisioning.reconcile_dkim(self.conn)
        self.assertTrue(engine_dkim.key_path(DOMAIN, "mm1").is_file())
        self.assertTrue(self.marker("mm1").is_file())

    # ── same-selector rotation is a different thing ──

    def test_a_same_selector_rotation_retires_nothing(self):
        """
        The filename does not change, so no worker can be looking for a name that
        vanished. `activate` uses an atomic rename, so a reader gets either the
        old inode or the new one and never a torn file.

        DNS is the real exposure here and it is NOT a filesystem problem:
        receivers that cached the old `p=` will reject the new signature until
        the record's TTL expires. That is a production rollover concern, not
        something local consistency can solve — see docs/NATIVE_MAIL_ENGINE.md.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        before = provisioning.get_dkim_public_key(self.conn, DOMAIN)

        provisioning.rotate_dkim_key(self.conn, DOMAIN, "mm1")
        after = provisioning.get_dkim_public_key(self.conn, DOMAIN)

        self.assertEqual(before["selector"], after["selector"])
        self.assertNotEqual(before["public_key"], after["public_key"])
        self.assertFalse(self.marker("mm1").is_file(),
                         "nothing was retired: the selector never changed")
        self.assertEqual([], engine_dkim.retired_selectors(DOMAIN))

    # ── the cryptographic half ──

    @unittest.skipUnless(DKIM_AVAILABLE, "dkimpy is not installed")
    def test_the_retired_key_still_produces_a_verifiable_signature(self):
        """
        The whole invariant, end to end: a message signed with the RETAINED key
        must verify against the public material published before the rotation.

        This is what a stale Rspamd worker would produce. Before retirement
        existed the key was already deleted and the worker had nothing to sign
        with, which is the unsigned window this closes.
        """
        provisioning.ensure_domain(self.conn, {"name": DOMAIN})
        before = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        provisioning.rotate_dkim_key(self.conn, DOMAIN, "alt7")
        after = provisioning.get_dkim_public_key(self.conn, DOMAIN)

        crlf = chr(13) + chr(10)
        message = (
            "From: alice@" + DOMAIN + crlf +
            "To: bob@" + DOMAIN + crlf +
            "Subject: retired selector" + crlf + crlf + "body" + crlf
        ).encode()

        def served(record_name, record_value):
            def dnsfunc(name, timeout=5):
                key = name.decode() if isinstance(name, bytes) else name
                if key.rstrip(".") != record_name:
                    raise dkimpy.KeyFormatError("no key served for " + str(key))
                return record_value.encode()
            return dnsfunc

        # Signed with the retired key, exactly as a stale worker would.
        retired_pem = engine_dkim.key_path(DOMAIN, "mm1").read_bytes()
        old_sig = dkimpy.sign(message, b"mm1", DOMAIN.encode(), retired_pem,
                              include_headers=[b"from", b"to", b"subject"])
        self.assertTrue(
            dkimpy.verify(old_sig + message, dnsfunc=served(
                before["dns_record_name"], before["dns_record_value"])),
            "a stale worker's signature must still verify against the old record",
        )

        # And the fresh path keeps working.
        new_pem = engine_dkim.key_path(DOMAIN, "alt7").read_bytes()
        new_sig = dkimpy.sign(message, b"alt7", DOMAIN.encode(), new_pem,
                              include_headers=[b"from", b"to", b"subject"])
        self.assertTrue(
            dkimpy.verify(new_sig + message, dnsfunc=served(
                after["dns_record_name"], after["dns_record_value"])))

        # The two keys are genuinely different, so the test above is not
        # accidentally verifying the same material twice.
        self.assertNotEqual(before["public_key"], after["public_key"])


class LastLoginRecordingTest(MailFlowDatabaseTestCase):
    def test_a_login_is_stamped(self):
        self.seed()
        self.assertTrue(provisioning.record_login(self.conn, f"alice@{DOMAIN}"))
        stamped = self.rows(
            "SELECT last_login_at FROM mailbox WHERE address = %s", f"alice@{DOMAIN}")[0][0]
        self.assertIsNotNone(stamped)

    def test_only_the_named_mailbox_moves(self):
        self.seed()
        provisioning.record_login(self.conn, f"alice@{DOMAIN}")
        self.assertIsNone(self.rows(
            "SELECT last_login_at FROM mailbox WHERE address = %s", f"bob@{DOMAIN}")[0][0])

    def test_an_unknown_address_is_not_an_error(self):
        """
        A mailbox deleted between authentication and the report is a race, not
        a failure worth propagating into a customer's session.
        """
        self.seed()
        self.assertFalse(provisioning.record_login(self.conn, f"ghost@{DOMAIN}"))

    def test_the_address_is_normalised_before_matching(self):
        self.seed()
        self.assertTrue(provisioning.record_login(self.conn, f"  ALICE@{DOMAIN.upper()}  "))


if __name__ == "__main__":                       # pragma: no cover
    unittest.main()
