"""
Native Engine provisioning (NE2).

WHAT THIS COVERS
    The engine-side state layer: domains, mailboxes, passwords, quotas, aliases,
    send-as authorisation, forwarding and the DKIM lifecycle — exercised against
    a REAL PostgreSQL database running the real migrations, not a mock.

WHY A REAL DATABASE
    Half of what NE2 promises is enforced BY the database: the uniqueness that
    makes concurrent provisioning safe, the CHECK that refuses an unhashed
    password, the coherence rule on domain storage, and the deliberate ABSENCE
    of a foreign key from dkim_key to domain. A mock would assert that the
    Python is self-consistent while proving nothing about any of that.

    The scratch database is created and dropped per class, named for the process
    so parallel runs cannot collide. It is never MateMail's database and never
    the engine's production database.

EVERY DOMAIN HERE IS .invalid
    Reserved by RFC 2606 and unresolvable by construction, so nothing in this
    file can touch real MateMail state or provision something that resolves.
"""
from __future__ import annotations

import logging
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
import urllib.parse

from django.test import SimpleTestCase

from apps.mail_engine.dto import DomainSpec, MailboxSpec
from tests.fake_native_engine import build_native_adapter
from tests.test_adapter_contract import AdapterContractTests

# The engine is a separate component with its own dependency set, so its modules
# are imported by path rather than as an installed package. This is the only
# place Django-side code reaches into `engine/`, and it reaches for TESTS only —
# nothing in `apps/` imports any of it.
ENGINE_DIR = pathlib.Path(__file__).resolve().parents[2] / "engine" / "native_api"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

try:
    import psycopg
    import db as engine_db
    import dkim as engine_dkim
    import passwords as engine_passwords
    import provisioning
    import validation
    from validation import ValidationError
    ENGINE_IMPORTABLE = True
    IMPORT_ERROR = ""
except Exception as exc:                                    # pragma: no cover
    ENGINE_IMPORTABLE = False
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

DOMAIN = "ne2.invalid"
ADDRESS = f"alice@{DOMAIN}"
OTHER = f"bob@{DOMAIN}"


def _admin_dsn() -> str | None:
    """
    Connection details for the PostgreSQL SERVER, taken from DATABASE_URL.

    The engine database is a different database on the same server in tests.
    Production keeps them on separate servers entirely; sharing one here is a
    test-environment convenience and never a statement about deployment.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    parts = urllib.parse.urlparse(url)
    if not parts.hostname:
        return None
    return (
        f"host={parts.hostname} port={parts.port or 5432} dbname=postgres "
        f"user={urllib.parse.unquote(parts.username or '')} "
        f"password={urllib.parse.unquote(parts.password or '')} connect_timeout=5"
    )


class EngineDatabaseTestCase(SimpleTestCase):
    """Creates a scratch engine database, migrates it, and drops it afterwards."""

    databases = set()                       # Django's DB fixtures are not used here

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not ENGINE_IMPORTABLE:
            raise unittest.SkipTest(f"engine modules not importable — {IMPORT_ERROR}")
        admin = _admin_dsn()
        if admin is None:
            raise unittest.SkipTest("DATABASE_URL is not set; no PostgreSQL to test against")

        cls.db_name = f"ne2_engine_test_{os.getpid()}"
        try:
            with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{cls.db_name}"')
                cur.execute(f'CREATE DATABASE "{cls.db_name}"')
        except psycopg.Error as exc:
            raise unittest.SkipTest(f"cannot create a scratch engine database: {exc}")

        cls._admin = admin
        cls.engine_dsn = admin.replace("dbname=postgres", f"dbname={cls.db_name}")

        # DKIM keys go to a throwaway directory, never the real key store.
        cls.key_dir = pathlib.Path(tempfile.mkdtemp(prefix="ne2-dkim-"))
        cls._real_key_dir = engine_dkim.KEY_DIR
        engine_dkim.KEY_DIR = cls.key_dir

        # The engine logs every key it mints. Useful in production, noise here.
        logging.getLogger("matemail.native.api").setLevel(logging.ERROR)
        logging.getLogger("matemail.native.api.db").setLevel(logging.ERROR)
        logging.getLogger("matemail.native.api.dkim").setLevel(logging.ERROR)
        logging.getLogger("matemail.native.api.provisioning").setLevel(logging.ERROR)

        cls.conn = psycopg.connect(cls.engine_dsn, autocommit=True)
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
        # Each test starts from an empty engine. TRUNCATE ... CASCADE rather than
        # dropping the schema, so the migration is exercised once per class.
        with self.conn.cursor() as cur:
            cur.execute(
                "TRUNCATE domain, mailbox, alias, alias_destination, forwarding, "
                "dkim_key RESTART IDENTITY CASCADE"
            )
        for stale in self.key_dir.glob("*"):
            stale.unlink()

    # ── helpers ─────────────────────────────────────────────────────────────

    def fail_on_sql(self, needle, error):
        """
        Raise `error` the first time a statement containing `needle` runs.

        Semantic rather than positional: it names the statement the failure
        lands on, so adding a lock — or any other query — cannot silently move
        the injection point somewhere else.
        """
        real_cursor = self.conn.cursor
        state = {"fired": False}

        def guard(text):
            if needle in text and not state["fired"]:
                state["fired"] = True
                raise error

        def patched(*a, **kw):
            return _CursorProxy(real_cursor(*a, **kw), guard)

        self.conn.cursor = patched
        self.addCleanup(lambda: setattr(self.conn, "cursor", real_cursor))

    def crash_on_dkim_row_write(self):
        """Process death on the statement that commits DKIM metadata."""
        self.fail_on_sql("INTO dkim_key", SimulatedCrash("process died at the row write"))


    def make_domain(self, name=DOMAIN, **kw):
        spec = {"name": name}
        spec.update(kw)
        return provisioning.ensure_domain(self.conn, spec)

    def make_mailbox(self, address=ADDRESS, password="Initial-Passphrase-1", **kw):
        spec = {"address": address}
        spec.update(kw)
        return provisioning.ensure_mailbox(self.conn, spec, password)

    def row(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


class SchemaTest(EngineDatabaseTestCase):
    def test_migrations_reach_the_required_version(self):
        self.assertGreaterEqual(engine_db.current_version(self.conn), engine_db.REQUIRED_VERSION)

    def test_migrations_are_idempotent(self):
        """Re-running against an already-migrated database must change nothing."""
        before = engine_db.current_version(self.conn)
        engine_db.apply_migrations(self.conn)
        engine_db.apply_migrations(self.conn)
        self.assertEqual(engine_db.current_version(self.conn), before)

    def test_upgrade_path_from_an_existing_version_1_database(self):
        """
        The MateServer database already carries version 1 from the NE1 init
        script. NE2 must upgrade THAT, not only build from empty — so this
        recreates the version-1 state exactly and migrates it forward.
        """
        scratch = f"{self.db_name}_v1"
        with psycopg.connect(self._admin, autocommit=True) as admin, admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
            cur.execute(f'CREATE DATABASE "{scratch}"')
        try:
            dsn = self._admin.replace("dbname=postgres", f"dbname={scratch}")
            with psycopg.connect(dsn, autocommit=True) as conn:
                # Exactly what the deployed NE1 bootstrap leaves behind.
                first = (ENGINE_DIR / "migrations" / "001_foundation.sql").read_text(encoding="utf-8")
                with conn.cursor() as cur:
                    cur.execute(first)
                self.assertEqual(engine_db.current_version(conn), 1)

                engine_db.apply_migrations(conn)
                self.assertEqual(engine_db.current_version(conn), engine_db.REQUIRED_VERSION)
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.mailbox') IS NOT NULL")
                    self.assertTrue(cur.fetchone()[0], "002 did not create the provisioning tables")
        finally:
            with psycopg.connect(self._admin, autocommit=True) as admin, admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')

    def test_dkim_key_has_no_foreign_key_to_domain(self):
        """
        Structural, not incidental. A cascade here would silently delete a
        signing key with its domain, break
        `test_deleting_a_domain_does_not_delete_its_dkim_key`, and remove the
        step that stops the next owner of a domain inheriting the last one's key.
        """
        found = self.row(
            """
            SELECT count(*) FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
            WHERE tc.table_name = 'dkim_key' AND tc.constraint_type = 'FOREIGN KEY'
            """
        )[0]
        self.assertEqual(found, 0, "dkim_key must not reference domain")

    def test_the_database_refuses_an_unhashed_password(self):
        self.make_domain()
        self.make_mailbox()
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "UPDATE mailbox SET password_hash = %s WHERE address = %s",
                    ("hunter2", ADDRESS),
                )

    def test_the_database_refuses_incoherent_domain_storage(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO domain (name, default_quota_mb, max_quota_mb, total_quota_mb) "
                    "VALUES ('bad.invalid', 5000, 1000, 10000)"
                )

    def test_the_database_refuses_private_material_in_the_public_key_column(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dkim_key (domain_name, selector, public_key, private_key_path) "
                    "VALUES ('x.invalid', 'mm1', "
                    "'-----BEGIN RSA PRIVATE KEY-----abc', '/tmp/x')"
                )


class DomainTest(EngineDatabaseTestCase):
    def test_creates_and_lists(self):
        self.make_domain()
        self.assertIn(DOMAIN, [d["name"] for d in provisioning.list_domains(self.conn)])

    def test_is_idempotent(self):
        for _ in range(3):
            self.make_domain()
        names = [d["name"] for d in provisioning.list_domains(self.conn)]
        self.assertEqual(names.count(DOMAIN), 1)

    def test_reconciles_a_changed_spec(self):
        self.make_domain(max_mailboxes=5)
        self.make_domain(max_mailboxes=50)
        self.assertEqual(self.row("SELECT max_mailboxes FROM domain WHERE name=%s", (DOMAIN,))[0], 50)

    def test_names_are_canonicalised(self):
        provisioning.ensure_domain(self.conn, {"name": "  NE2.INVALID.  "})
        self.assertIn("ne2.invalid", [d["name"] for d in provisioning.list_domains(self.conn)])

    def test_set_active_is_idempotent_assignment(self):
        self.make_domain()
        provisioning.set_domain_active(self.conn, DOMAIN, False)
        provisioning.set_domain_active(self.conn, DOMAIN, False)
        self.assertFalse(next(d for d in provisioning.list_domains(self.conn)
                              if d["name"] == DOMAIN)["active"])
        provisioning.set_domain_active(self.conn, DOMAIN, True)
        self.assertTrue(next(d for d in provisioning.list_domains(self.conn)
                             if d["name"] == DOMAIN)["active"])

    def test_delete_is_idempotent(self):
        self.make_domain()
        provisioning.delete_domain(self.conn, DOMAIN)
        provisioning.delete_domain(self.conn, DOMAIN)
        provisioning.delete_domain(self.conn, "never-existed.invalid")
        self.assertNotIn(DOMAIN, [d["name"] for d in provisioning.list_domains(self.conn)])

    def test_delete_removes_dependent_state(self):
        self.make_domain()
        self.make_mailbox()
        provisioning.ensure_alias(self.conn, {"address": f"sales@{DOMAIN}", "destinations": [ADDRESS]})
        provisioning.delete_domain(self.conn, DOMAIN)
        self.assertEqual(self.row("SELECT count(*) FROM mailbox")[0], 0)
        self.assertEqual(self.row("SELECT count(*) FROM alias")[0], 0)

    def test_invalid_names_are_rejected(self):
        for bad in ("", "no-tld", "-leading.invalid", "spa ce.invalid", "münchen.invalid", 42):
            with self.subTest(name=bad), self.assertRaises(ValidationError):
                provisioning.ensure_domain(self.conn, {"name": bad})

    def test_incoherent_storage_is_rejected_with_a_useful_error(self):
        with self.assertRaises(ValidationError):
            self.make_domain(default_quota_mb=5000, max_quota_mb=1000, total_quota_mb=10000)

    def test_setting_active_on_an_unknown_domain_is_not_found(self):
        with self.assertRaises(provisioning.NotFound):
            provisioning.set_domain_active(self.conn, "never.invalid", True)


class MailboxTest(EngineDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.make_domain()

    def test_creates_and_lists(self):
        self.make_mailbox()
        self.assertIn(ADDRESS, [m["address"] for m in provisioning.list_mailboxes(self.conn)])

    def test_is_idempotent(self):
        self.make_mailbox()
        self.make_mailbox()
        rows = [m for m in provisioning.list_mailboxes(self.conn) if m["address"] == ADDRESS]
        self.assertEqual(len(rows), 1)

    def test_requires_an_existing_domain(self):
        with self.assertRaises(provisioning.NotFound):
            provisioning.ensure_mailbox(
                self.conn, {"address": "nobody@unprovisioned.invalid"}, "Initial-Passphrase-1"
            )

    def test_a_new_mailbox_requires_a_password(self):
        with self.assertRaises(ValidationError):
            provisioning.ensure_mailbox(self.conn, {"address": ADDRESS}, "")

    def test_retry_without_a_password_keeps_the_credential(self):
        """The documented guarantee: a retry must not lock the customer out."""
        self.make_mailbox()
        before = self.row("SELECT password_hash FROM mailbox WHERE address=%s", (ADDRESS,))[0]
        provisioning.ensure_mailbox(self.conn, {"address": ADDRESS, "display_name": "Renamed"}, "")
        after = self.row("SELECT password_hash FROM mailbox WHERE address=%s", (ADDRESS,))[0]
        self.assertEqual(before, after)
        self.assertTrue(after)

    def test_quota_updates_through_ensure_and_directly(self):
        self.make_mailbox(quota_mb=1024)
        self.make_mailbox(quota_mb=4096, password="")
        self.assertEqual(self.row("SELECT quota_mb FROM mailbox WHERE address=%s", (ADDRESS,))[0], 4096)
        provisioning.set_mailbox_quota(self.conn, ADDRESS, 8192)
        provisioning.set_mailbox_quota(self.conn, ADDRESS, 8192)
        self.assertEqual(self.row("SELECT quota_mb FROM mailbox WHERE address=%s", (ADDRESS,))[0], 8192)

    def test_quota_is_megabytes_and_not_reinterpreted(self):
        """
        The unit is MB, matching MailboxSpec.quota_mb. Storing bytes would make
        a 2048 MB mailbox a 2 KB one; storing GB would make it 2 TB. The stored
        integer must be exactly what the caller passed.
        """
        self.make_mailbox(quota_mb=2048)
        self.assertEqual(self.row("SELECT quota_mb FROM mailbox WHERE address=%s", (ADDRESS,))[0], 2048)

    def test_invalid_quotas_are_rejected(self):
        self.make_mailbox()
        for bad in (0, -1, 2 ** 40, "1024", 10.5, True, None):
            with self.subTest(quota=bad), self.assertRaises(ValidationError):
                provisioning.set_mailbox_quota(self.conn, ADDRESS, bad)

    def test_set_active_is_assignment(self):
        self.make_mailbox()
        provisioning.set_mailbox_active(self.conn, ADDRESS, False)
        self.assertFalse(self.row("SELECT active FROM mailbox WHERE address=%s", (ADDRESS,))[0])

    def test_an_inactive_mailbox_is_still_represented(self):
        self.make_mailbox()
        provisioning.set_mailbox_active(self.conn, ADDRESS, False)
        self.assertIn(ADDRESS, [m["address"] for m in provisioning.list_mailboxes(self.conn)])

    def test_delete_is_idempotent(self):
        self.make_mailbox()
        provisioning.delete_mailbox(self.conn, ADDRESS)
        provisioning.delete_mailbox(self.conn, ADDRESS)
        provisioning.delete_mailbox(self.conn, "nobody@never-existed.invalid")
        self.assertNotIn(ADDRESS, [m["address"] for m in provisioning.list_mailboxes(self.conn)])

    def test_list_scopes_to_a_domain(self):
        provisioning.ensure_domain(self.conn, {"name": "other.invalid"})
        self.make_mailbox()
        provisioning.ensure_mailbox(
            self.conn, {"address": "bob@other.invalid"}, "Initial-Passphrase-1"
        )
        scoped = [m["address"] for m in provisioning.list_mailboxes(self.conn, DOMAIN)]
        self.assertEqual(scoped, [ADDRESS])

    def test_address_and_declared_domain_must_agree(self):
        with self.assertRaises(ValidationError):
            provisioning.ensure_mailbox(
                self.conn, {"address": ADDRESS, "domain": "other.invalid"}, "Initial-Passphrase-1"
            )

    def test_invalid_addresses_are_rejected(self):
        for bad in ("", "no-at-sign", "@nolocal.invalid", "a@@b.invalid", "x@bad", 7):
            with self.subTest(address=bad), self.assertRaises(ValidationError):
                provisioning.ensure_mailbox(self.conn, {"address": bad}, "Initial-Passphrase-1")


class PasswordTest(EngineDatabaseTestCase):
    """The security-critical path: plaintext in, hash stored, nothing echoed."""

    PLAINTEXT = "Correct-Horse-Battery-Staple-9"

    def setUp(self):
        super().setUp()
        self.make_domain()

    def test_the_stored_value_is_a_blf_crypt_hash(self):
        self.make_mailbox(password=self.PLAINTEXT)
        stored = self.row("SELECT password_hash FROM mailbox WHERE address=%s", (ADDRESS,))[0]
        self.assertTrue(stored.startswith("{BLF-CRYPT}$2"), stored[:20])

    def test_the_plaintext_is_never_stored_anywhere(self):
        """
        Searches every text column of every table, not just the one we expect to
        be safe. A password leaking into display_name or an alias destination
        would be just as bad as leaking into password_hash.
        """
        self.make_mailbox(password=self.PLAINTEXT)
        provisioning.ensure_alias(self.conn, {"address": f"s@{DOMAIN}", "destinations": [ADDRESS]})
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND data_type IN ('text','character varying')"
            )
            columns = cur.fetchall()
        self.assertTrue(columns, "no text columns found — the search would be vacuous")
        for table, column in columns:
            with self.conn.cursor() as cur:
                cur.execute(
                    f'SELECT count(*) FROM "{table}" WHERE "{column}" LIKE %s',
                    (f"%{self.PLAINTEXT}%",),
                )
                self.assertEqual(
                    cur.fetchone()[0], 0,
                    f"plaintext password found in {table}.{column}",
                )

    def test_the_hash_verifies_the_right_password_and_rejects_others(self):
        self.make_mailbox(password=self.PLAINTEXT)
        stored = self.row("SELECT password_hash FROM mailbox WHERE address=%s", (ADDRESS,))[0]
        self.assertTrue(engine_passwords.verify_password(self.PLAINTEXT, stored))
        self.assertFalse(engine_passwords.verify_password("not-the-password", stored))
        self.assertFalse(engine_passwords.verify_password("", stored))

    def test_replacement_changes_the_stored_hash(self):
        self.make_mailbox(password=self.PLAINTEXT)
        first = self.row("SELECT password_hash FROM mailbox WHERE address=%s", (ADDRESS,))[0]
        provisioning.set_mailbox_password(self.conn, ADDRESS, "A-Different-Passphrase-2")
        second = self.row("SELECT password_hash FROM mailbox WHERE address=%s", (ADDRESS,))[0]
        self.assertNotEqual(first, second)
        self.assertTrue(engine_passwords.verify_password("A-Different-Passphrase-2", second))
        self.assertFalse(engine_passwords.verify_password(self.PLAINTEXT, second))

    def test_the_same_password_hashes_differently_each_time(self):
        """A shared salt would let one cracked hash unlock every reuse of it."""
        a = engine_passwords.hash_password(self.PLAINTEXT)
        b = engine_passwords.hash_password(self.PLAINTEXT)
        self.assertNotEqual(a, b)

    def test_an_empty_password_is_refused(self):
        self.make_mailbox()
        with self.assertRaises(ValidationError):
            provisioning.set_mailbox_password(self.conn, ADDRESS, "")
        with self.assertRaises(ValueError):
            engine_passwords.hash_password("")

    def test_errors_never_echo_the_password(self):
        """
        A rejection must describe the PROBLEM, never the value. An error saying
        "'hunter2' is too short" puts the credential into every log that records
        the response.

        Only distinctive values are checked: the empty string is a substring of
        every message, so asserting its absence can never pass and would prove
        nothing if it did.
        """
        self.make_mailbox()
        for bad in ("Sup3rSecret-Never-Echo-Me", 12345, "z" * 2000):
            with self.subTest(value=type(bad).__name__):
                try:
                    provisioning.set_mailbox_password(self.conn, ADDRESS, bad)
                except Exception as exc:
                    self.assertNotIn(str(bad), str(exc))

    def test_setting_a_password_on_an_unknown_mailbox_is_not_found(self):
        with self.assertRaises(provisioning.NotFound):
            provisioning.set_mailbox_password(self.conn, "nobody@ne2.invalid", "Passphrase-3")


class AliasAndSendAsTest(EngineDatabaseTestCase):
    """
    The distinction NE0 asked for: receiving is not sending.

    mailcow's shape — an alias that delivers but whose owner cannot send as it —
    is what these tests exist to prevent, along with its mirror image: a
    destination that gains a sending right merely by being a delivery target.
    """

    SALES = f"sales@{DOMAIN}"
    EXTERNAL = "someone@external.invalid"

    def setUp(self):
        super().setUp()
        self.make_domain()
        self.make_mailbox()

    def test_creates_and_is_idempotent(self):
        spec = {"address": self.SALES, "destinations": [ADDRESS]}
        provisioning.ensure_alias(self.conn, spec)
        provisioning.ensure_alias(self.conn, spec)
        self.assertEqual(self.row("SELECT count(*) FROM alias WHERE address=%s", (self.SALES,))[0], 1)
        self.assertEqual(provisioning.get_alias(self.conn, self.SALES)["destinations"], [ADDRESS])

    def test_replaces_the_destination_set_rather_than_merging(self):
        provisioning.ensure_alias(self.conn, {"address": self.SALES, "destinations": [ADDRESS]})
        provisioning.ensure_alias(
            self.conn, {"address": self.SALES, "destinations": ["a@x.invalid", "b@x.invalid"]}
        )
        self.assertEqual(
            provisioning.get_alias(self.conn, self.SALES)["destinations"],
            ["a@x.invalid", "b@x.invalid"],
        )

    def test_an_internal_destination_confers_send_as(self):
        provisioning.ensure_alias(self.conn, {"address": self.SALES, "destinations": [ADDRESS]})
        self.assertEqual(provisioning.authorized_send_as(self.conn, ADDRESS), [self.SALES])

    def test_an_external_destination_confers_nothing(self):
        """
        The mirror hazard. An alias delivering to an outside address must not
        make that address — or anyone else — an authorised sender.
        """
        provisioning.ensure_alias(
            self.conn, {"address": self.SALES, "destinations": [self.EXTERNAL]}
        )
        found = provisioning.get_alias(self.conn, self.SALES)
        self.assertEqual(found["destinations"], [self.EXTERNAL])
        self.assertEqual(found["authorized_senders"], [])
        self.assertEqual(provisioning.authorized_send_as(self.conn, ADDRESS), [])

    def test_send_as_does_not_leak_across_mailboxes(self):
        self.make_mailbox(address=OTHER)
        provisioning.ensure_alias(self.conn, {"address": self.SALES, "destinations": [ADDRESS]})
        self.assertEqual(provisioning.authorized_send_as(self.conn, ADDRESS), [self.SALES])
        self.assertEqual(provisioning.authorized_send_as(self.conn, OTHER), [])

    def test_a_mailbox_created_after_the_alias_still_gains_send_as(self):
        """
        Ordering must not decide an authorisation question. Creating the alias
        first left `mailbox_id` NULL until re-linking was added.
        """
        late = f"late@{DOMAIN}"
        provisioning.ensure_alias(self.conn, {"address": self.SALES, "destinations": [late]})
        self.assertEqual(provisioning.authorized_send_as(self.conn, ADDRESS), [])
        self.make_mailbox(address=late)
        self.assertEqual(provisioning.authorized_send_as(self.conn, late), [self.SALES])

    def test_deleting_the_mailbox_withdraws_send_as(self):
        provisioning.ensure_alias(self.conn, {"address": self.SALES, "destinations": [ADDRESS]})
        provisioning.delete_mailbox(self.conn, ADDRESS)
        self.assertEqual(
            self.row("SELECT count(*) FROM alias_destination WHERE mailbox_id IS NOT NULL")[0], 0
        )

    def test_an_inactive_alias_authorises_nobody(self):
        provisioning.ensure_alias(
            self.conn, {"address": self.SALES, "destinations": [ADDRESS], "active": False}
        )
        self.assertEqual(provisioning.authorized_send_as(self.conn, ADDRESS), [])

    def test_an_inactive_mailbox_authorises_nobody(self):
        provisioning.ensure_alias(self.conn, {"address": self.SALES, "destinations": [ADDRESS]})
        provisioning.set_mailbox_active(self.conn, ADDRESS, False)
        self.assertEqual(provisioning.authorized_send_as(self.conn, ADDRESS), [])

    def test_delete_is_idempotent(self):
        provisioning.ensure_alias(self.conn, {"address": self.SALES, "destinations": [ADDRESS]})
        provisioning.delete_alias(self.conn, self.SALES)
        provisioning.delete_alias(self.conn, self.SALES)
        self.assertIsNone(provisioning.get_alias(self.conn, self.SALES))

    def test_an_alias_requires_a_destination(self):
        with self.assertRaises(ValidationError):
            provisioning.ensure_alias(self.conn, {"address": self.SALES, "destinations": []})

    def test_duplicate_destinations_collapse(self):
        provisioning.ensure_alias(
            self.conn, {"address": self.SALES, "destinations": [ADDRESS, ADDRESS, ADDRESS]}
        )
        self.assertEqual(provisioning.get_alias(self.conn, self.SALES)["destinations"], [ADDRESS])


class ForwardingTest(EngineDatabaseTestCase):
    EXTERNAL = "external@gmail.invalid"

    def setUp(self):
        super().setUp()
        self.make_domain()
        self.make_mailbox()

    def test_creates_and_is_idempotent(self):
        spec = {"mailbox_address": ADDRESS, "destinations": [self.EXTERNAL]}
        provisioning.ensure_forwarding(self.conn, spec)
        provisioning.ensure_forwarding(self.conn, spec)
        self.assertEqual(provisioning.get_forwarding(self.conn, ADDRESS), [self.EXTERNAL])

    def test_applies_destinations_verbatim_and_in_order(self):
        provisioning.ensure_forwarding(
            self.conn, {"mailbox_address": ADDRESS, "destinations": [self.EXTERNAL, ADDRESS]}
        )
        self.assertEqual(provisioning.get_forwarding(self.conn, ADDRESS), [self.EXTERNAL, ADDRESS])

    def test_an_empty_set_removes_forwarding(self):
        provisioning.ensure_forwarding(
            self.conn, {"mailbox_address": ADDRESS, "destinations": [self.EXTERNAL]}
        )
        provisioning.ensure_forwarding(self.conn, {"mailbox_address": ADDRESS, "destinations": []})
        self.assertEqual(provisioning.get_forwarding(self.conn, ADDRESS), [])

    def test_forwarding_does_not_create_an_alias(self):
        provisioning.ensure_forwarding(
            self.conn, {"mailbox_address": ADDRESS, "destinations": [self.EXTERNAL]}
        )
        self.assertEqual(self.row("SELECT count(*) FROM alias")[0], 0)
        self.assertEqual(self.row("SELECT count(*) FROM alias_destination")[0], 0)

    def test_forwarding_never_confers_send_as(self):
        """
        support@customer -> external@gmail must NOT make the external address an
        authorised sender, and must not give the forwarding mailbox any new
        identity either.
        """
        provisioning.ensure_forwarding(
            self.conn, {"mailbox_address": ADDRESS, "destinations": [self.EXTERNAL]}
        )
        self.assertEqual(provisioning.authorized_send_as(self.conn, ADDRESS), [])

    def test_send_as_stays_empty_even_with_forwarding_rows_present(self):
        """
        The previous test creates no alias at all, so an implementation that
        wrongly unioned the forwarding table would still return nothing and the
        test would pass. This one makes forwarding rows EXIST and a legitimate
        alias exist alongside them, so the send-as answer must contain the alias
        and nothing from forwarding.
        """
        provisioning.ensure_forwarding(self.conn, {
            "mailbox_address": ADDRESS,
            "destinations": [self.EXTERNAL, "second@elsewhere.invalid"],
        })
        provisioning.ensure_alias(
            self.conn, {"address": f"sales@{DOMAIN}", "destinations": [ADDRESS]}
        )
        self.assertEqual(self.row("SELECT count(*) FROM forwarding")[0], 2)
        self.assertEqual(provisioning.authorized_send_as(self.conn, ADDRESS),
                         [f"sales@{DOMAIN}"])

    def test_requires_an_existing_mailbox(self):
        with self.assertRaises(provisioning.NotFound):
            provisioning.ensure_forwarding(
                self.conn, {"mailbox_address": "nobody@ne2.invalid", "destinations": [self.EXTERNAL]}
            )

    def test_invalid_destinations_are_rejected(self):
        with self.assertRaises(ValidationError):
            provisioning.ensure_forwarding(
                self.conn, {"mailbox_address": ADDRESS, "destinations": ["not-an-address"]}
            )


class DkimTest(EngineDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.make_domain()

    def test_creating_a_domain_also_creates_its_key(self):
        info = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        self.assertIsNotNone(info)
        self.assertTrue(info["public_key"])

    def test_the_selector_on_the_spec_is_the_one_used(self):
        provisioning.ensure_domain(self.conn, {"name": "sel.invalid", "dkim_selector": "sel7"})
        info = provisioning.get_dkim_public_key(self.conn, "sel.invalid")
        self.assertEqual(info["selector"], "sel7")
        self.assertEqual(info["dns_record_name"], "sel7._domainkey.sel.invalid")
        self.assertIn("v=DKIM1", info["dns_record_value"])

    def test_none_for_a_domain_the_engine_does_not_hold(self):
        self.assertIsNone(provisioning.get_dkim_public_key(self.conn, "never.invalid"))

    def test_the_private_key_is_on_disk_and_never_in_the_database(self):
        path = self.row("SELECT private_key_path FROM dkim_key WHERE domain_name=%s", (DOMAIN,))[0]
        self.assertTrue(pathlib.Path(path).is_file())
        material = pathlib.Path(path).read_text()
        self.assertIn("PRIVATE KEY", material)
        stored_public = self.row("SELECT public_key FROM dkim_key WHERE domain_name=%s", (DOMAIN,))[0]
        self.assertNotIn("PRIVATE KEY", stored_public)

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits are not meaningful on Windows")
    def test_the_private_key_is_mode_0600(self):
        path = self.row("SELECT private_key_path FROM dkim_key WHERE domain_name=%s", (DOMAIN,))[0]
        self.assertEqual(engine_dkim.private_key_mode(path), 0o600)

    def test_the_returned_material_carries_nothing_private(self):
        info = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        for forbidden in ("private_key", "privkey", "private", "secret", "pem", "private_key_path"):
            self.assertNotIn(forbidden, info)
        blob = " ".join(str(v) for v in info.values()).upper()
        self.assertNotIn("PRIVATE KEY", blob)
        self.assertNotIn("BEGIN RSA", blob)

    def test_the_key_is_at_least_2048_bits(self):
        self.assertGreaterEqual(
            self.row("SELECT key_size FROM dkim_key WHERE domain_name=%s", (DOMAIN,))[0], 2048
        )

    def test_a_weak_key_is_refused(self):
        with self.assertRaises(engine_dkim.DkimError):
            engine_dkim.generate("weak.invalid", "mm1", key_size=1024)

    def test_rotation_produces_new_material_and_is_not_idempotent(self):
        first = provisioning.get_dkim_public_key(self.conn, DOMAIN)["public_key"]
        second = provisioning.rotate_dkim_key(self.conn, DOMAIN)["public_key"]
        third = provisioning.rotate_dkim_key(self.conn, DOMAIN)["public_key"]
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)

    def test_get_after_rotation_matches(self):
        rotated = provisioning.rotate_dkim_key(self.conn, DOMAIN)
        self.assertEqual(provisioning.get_dkim_public_key(self.conn, DOMAIN)["public_key"],
                         rotated["public_key"])

    def test_rotation_leaves_exactly_one_generation(self):
        """
        No orphans: superseded private keys must not linger.

        Two files remain by design — the active key and the immutable generation
        it is hard-linked to. That pair IS the live key, not a leftover; the
        generation name is what makes "which one is live" an inode identity
        rather than a record someone has to keep in step.
        """
        provisioning.rotate_dkim_key(self.conn, DOMAIN, selector="rot2")
        provisioning.rotate_dkim_key(self.conn, DOMAIN, selector="rot3")
        files = sorted(p.name for p in self.key_dir.glob(f"{DOMAIN}.*"))
        generations = [f for f in files if ".g" in f]
        self.assertEqual(len(generations), 1, f"orphaned generations: {files}")
        self.assertIn(f"{DOMAIN}.rot3.key", files)

        # NE3: the superseded selector's ACTIVE key is deliberately retained, so
        # an Rspamd worker still holding the previous selector map signs
        # correctly instead of sending unsigned. What must NOT survive is the
        # superseded selector's GENERATIONS — nothing opens those by name — and
        # the retention must be bounded by a retirement marker rather than open
        # ended.
        self.assertIn(f"{DOMAIN}.rot2.key", files,
                      "the outgoing key must be retained for stale workers")
        self.assertTrue(engine_dkim.retirement_marker_path(DOMAIN, "rot2").is_file(),
                        "a retained key without a marker has no lifetime rule")
        self.assertEqual(engine_dkim.generations_for(DOMAIN, "rot2"), [],
                         f"superseded generations were left behind: {files}")
        self.assertEqual(
            provisioning.get_dkim_public_key(self.conn, DOMAIN)["selector"], "rot3",
            "the retained key must never be authoritative",
        )
        # And the pair is one key, not two.
        active = self.key_dir / f"{DOMAIN}.rot3.key"
        import os as _os
        self.assertEqual(_os.stat(active).st_ino,
                         _os.stat(self.key_dir / generations[0]).st_ino)

    def test_rotation_keeps_the_old_key_when_the_database_write_fails(self):
        """
        The failure that must never produce a domain with no usable key. The
        write is made to fail after generation; the previous key and its row
        must both survive.
        """
        before = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        before_path = self.row("SELECT private_key_path FROM dkim_key WHERE domain_name=%s",
                               (DOMAIN,))[0]

        class Boom(Exception):
            pass

        real_cursor = self.conn.cursor
        state = {"fired": False}

        def guard(text):
            # Named by the statement, not by call order: the advisory lock adds
            # cursor uses, and a positional injection would quietly land on it.
            if "INTO dkim_key" in text and not state["fired"]:
                state["fired"] = True
                raise Boom("simulated database failure")

        self.conn.cursor = lambda *a, **kw: _CursorProxy(real_cursor(*a, **kw), guard)
        try:
            with self.assertRaises(Boom):
                provisioning.rotate_dkim_key(self.conn, DOMAIN)
        finally:
            self.conn.cursor = real_cursor

        after = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        self.assertEqual(after["public_key"], before["public_key"], "the working key was lost")
        self.assertTrue(pathlib.Path(before_path).is_file(), "the working key file was deleted")
        # The new generation must be gone; the original generation and its
        # active link are the only things left.
        generations = sorted(p.name for p in self.key_dir.glob(f"{DOMAIN}.*.g*.key"))
        self.assertEqual(generations, [pathlib.Path(before_path).name],
                         f"a private key was left orphaned: {generations}")

    def test_delete_removes_file_and_metadata_and_is_idempotent(self):
        path = self.row("SELECT private_key_path FROM dkim_key WHERE domain_name=%s", (DOMAIN,))[0]
        provisioning.delete_dkim_key(self.conn, DOMAIN)
        self.assertIsNone(provisioning.get_dkim_public_key(self.conn, DOMAIN))
        self.assertFalse(pathlib.Path(path).exists())
        provisioning.delete_dkim_key(self.conn, DOMAIN)
        provisioning.delete_dkim_key(self.conn, "never.invalid")

    def test_deleting_a_domain_does_NOT_delete_its_key(self):
        """
        Pinned by the adapter contract and depended on by
        `remove_domain_from_engine`, which deletes the key explicitly BEFORE the
        domain. Cascading here would look tidier and quietly break both.
        """
        original = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        provisioning.delete_domain(self.conn, DOMAIN)
        survivor = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        self.assertIsNotNone(survivor)
        self.assertEqual(survivor["public_key"], original["public_key"])

    def test_recreating_a_domain_inherits_a_surviving_key(self):
        """The cross-tenant hazard itself, reproduced so it cannot be forgotten."""
        leaked = provisioning.get_dkim_public_key(self.conn, DOMAIN)["public_key"]
        provisioning.delete_domain(self.conn, DOMAIN)
        provisioning.ensure_domain(self.conn, {"name": DOMAIN, "dkim_selector": "tenantb"})
        self.assertEqual(provisioning.get_dkim_public_key(self.conn, DOMAIN)["public_key"], leaked)

        # And the remedy the deprovisioning task actually performs.
        provisioning.delete_domain(self.conn, DOMAIN)
        provisioning.delete_dkim_key(self.conn, DOMAIN)
        provisioning.ensure_domain(self.conn, {"name": DOMAIN, "dkim_selector": "tenantb"})
        clean = provisioning.get_dkim_public_key(self.conn, DOMAIN)
        self.assertNotEqual(clean["public_key"], leaked)
        self.assertEqual(clean["selector"], "tenantb")

    def test_invalid_selectors_are_rejected(self):
        for bad in ("has space", "-leading", "under_score", "a" * 64, 5):
            with self.subTest(selector=bad), self.assertRaises(ValidationError):
                provisioning.rotate_dkim_key(self.conn, DOMAIN, selector=bad)

    def test_an_empty_selector_means_keep_the_current_one(self):
        """
        Not an invalid selector. The adapter signature is
        `rotate_dkim_key(domain, selector="")`, where the default means "the one
        already in use" — rotation must not silently move a domain onto a
        selector its published DNS does not name.
        """
        provisioning.ensure_domain(self.conn, {"name": "keep.invalid", "dkim_selector": "keepme"})
        rotated = provisioning.rotate_dkim_key(self.conn, "keep.invalid", selector="")
        self.assertEqual(rotated["selector"], "keepme")


class ConcurrencyTest(EngineDatabaseTestCase):
    """
    Two identical provisioning requests arriving together must both succeed.

    `if not exists: insert` loses this race and surfaces a unique violation to a
    caller that did nothing wrong. These use SEPARATE connections, because a
    race on one connection is not a race.
    """

    def _connections(self, count):
        conns = [psycopg.connect(self.engine_dsn, autocommit=True) for _ in range(count)]
        self.addCleanup(lambda: [c.close() for c in conns])
        return conns

    def test_uniqueness_is_enforced_by_the_database(self):
        self.make_domain()
        domain_id = self.row("SELECT id FROM domain WHERE name=%s", (DOMAIN,))[0]
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO mailbox (domain_id, address, local_part, password_hash) "
                    "VALUES (%s, %s, 'alice', '{BLF-CRYPT}$2b$10$x')",
                    (domain_id, ADDRESS),
                )
                cur.execute(
                    "INSERT INTO mailbox (domain_id, address, local_part, password_hash) "
                    "VALUES (%s, %s, 'alice', '{BLF-CRYPT}$2b$10$x')",
                    (domain_id, ADDRESS),
                )

    def test_concurrent_ensure_domain_both_succeed(self):
        import threading

        conns = self._connections(6)
        errors = []

        def worker(conn):
            try:
                provisioning.ensure_domain(conn, {"name": "race.invalid"})
            except Exception as exc:                       # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(c,)) for c in conns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"concurrent ensure_domain failed: {errors}")
        self.assertEqual(self.row("SELECT count(*) FROM domain WHERE name='race.invalid'")[0], 1)
        # Exactly one DKIM key, and no orphaned private files from the losers.
        self.assertEqual(self.row("SELECT count(*) FROM dkim_key WHERE domain_name='race.invalid'")[0], 1)
        generations = sorted(p.name for p in self.key_dir.glob("race.invalid.*.g*.key"))
        self.assertEqual(len(generations), 1,
                         f"the losing requests left orphaned generations: {generations}")
        self.assertTrue((self.key_dir / "race.invalid.mm1.key").is_file(),
                        "the winning key is not live")

    def test_concurrent_ensure_mailbox_both_succeed(self):
        import threading

        self.make_domain()
        conns = self._connections(6)
        errors = []

        def worker(conn):
            try:
                provisioning.ensure_mailbox(
                    conn, {"address": ADDRESS}, "Initial-Passphrase-1"
                )
            except Exception as exc:                       # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(c,)) for c in conns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"concurrent ensure_mailbox failed: {errors}")
        self.assertEqual(self.row("SELECT count(*) FROM mailbox WHERE address=%s", (ADDRESS,))[0], 1)


class _CursorProxy:
    """
    Forwards to a real cursor, letting a guard inspect each statement first.

    Needed because the injection points have to be SEMANTIC. Counting
    `conn.cursor()` calls looked simpler and was brittle: adding the advisory
    lock inserted two more cursor uses per operation and silently moved every
    numbered injection point onto the wrong statement, so tests kept passing
    while testing something else.
    """

    def __init__(self, inner, guard):
        self._inner = inner
        self._guard = guard

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._inner.__exit__(*exc_info)

    def execute(self, query, params=None, *args, **kwargs):
        self._guard(query if isinstance(query, str) else str(query))
        return self._inner.execute(query, params, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __iter__(self):
        return iter(self._inner)


class SimulatedCrash(BaseException):
    """
    Process death, not a handled error.

    Deliberately derived from BaseException so that no `except Exception:`
    cleanup path in the engine runs. A test that raised a normal Exception would
    exercise the rollback handlers and prove nothing about what a container
    being killed actually leaves behind.
    """


class DkimCrashConsistencyTest(EngineDatabaseTestCase):
    """
    The invariant, enforced by injection at every step of the lifecycle:

        after process death at ANY point, the domain has exactly ONE usable
        DKIM generation, and the public key MateMail would publish is derived
        from the private key Rspamd would sign with.

    Never: the database describing one generation while the live key is another.
    """

    def crash_on_activate(self):
        """
        Kill the process just before the key becomes live.

        Fires ONCE. Reconciliation models what a restarted process does, and a
        restarted process is not still crashing — leaving the patch armed would
        test a permanently broken engine instead of recovery from one crash.
        """
        real = engine_dkim.activate
        state = {"fired": False}

        def exploding(*a, **kw):
            if not state["fired"]:
                state["fired"] = True
                raise SimulatedCrash("process died before activation")
            return real(*a, **kw)

        engine_dkim.activate = exploding
        self.addCleanup(lambda: setattr(engine_dkim, "activate", real))

    # ── the property every case must end in ─────────────────────────────────

    def assert_consistent(self, domain, expect_public=None, expect_none=False):
        """
        The whole point, asserted the same way every time.

        Checks that what `get_dkim_public_key` reports is exactly what the live
        private key produces, and that the stored row agrees with both.
        """
        reported = provisioning.get_dkim_public_key(self.conn, domain)
        if expect_none:
            self.assertIsNone(reported, "expected no usable key")
            return None

        self.assertIsNotNone(reported, "domain has no usable DKIM key")
        selector = reported["selector"]
        derived = engine_dkim.public_material_of(engine_dkim.key_path(domain, selector))
        self.assertIsNotNone(derived, "no live private key on disk")
        self.assertEqual(
            reported["public_key"], derived,
            "get_dkim_public_key does not match the key that would actually sign",
        )
        stored = self.row(
            "SELECT public_key FROM dkim_key WHERE domain_name=%s", (domain,)
        )[0]
        self.assertEqual(stored, derived, "the stored row does not describe the live key")
        self.assertIn("p=" + derived, reported["dns_record_value"])
        if expect_public is not None:
            self.assertEqual(derived, expect_public)
        return derived

    def live_files(self, domain, selector):
        return sorted(p.name for p in self.key_dir.glob(f"{domain}.{selector}*"))

    # ── first-time creation ─────────────────────────────────────────────────

    def test_crash_after_generation_before_the_row_is_claimed(self):
        """Nothing was committed, so the domain simply has no key yet."""
        self.crash_on_dkim_row_write()
        with self.assertRaises(SimulatedCrash):
            self.make_domain()

        self.assertEqual(self.row("SELECT count(*) FROM dkim_key")[0], 0)
        report = provisioning.reconcile_dkim(self.conn)
        self.assertTrue(report["orphans_removed"], "the orphan generation was not cleaned up")
        self.assertEqual(self.live_files(DOMAIN, "mm1"), [],
                         "a private key was left behind with nothing referring to it")

    def test_crash_after_the_row_is_claimed_before_activation(self):
        """
        A row exists but no key is live. The honest answer is "no usable key",
        never a stale public key — and reconciliation completes the activation
        from the generation the row names.
        """
        self.crash_on_activate()
        with self.assertRaises(SimulatedCrash):
            self.make_domain()

        self.assertEqual(self.row("SELECT count(*) FROM dkim_key")[0], 1)
        self.assert_consistent(DOMAIN, expect_none=True)

        report = provisioning.reconcile_dkim(self.conn)
        self.assertIn(DOMAIN, report["activated"])
        self.assert_consistent(DOMAIN)

    def test_a_completed_creation_is_consistent(self):
        self.make_domain()
        self.assert_consistent(DOMAIN)

    # ── rotation, same selector: the case the old design got wrong ──────────

    def test_crash_during_same_selector_rotation_before_activation(self):
        """
        The old generation must still be live and fully usable. This is the
        exact case the previous implementation destroyed: it computed the same
        filename and overwrote the working key before committing anything.
        """
        self.make_domain()
        original = self.assert_consistent(DOMAIN)

        self.crash_on_activate()
        with self.assertRaises(SimulatedCrash):
            provisioning.rotate_dkim_key(self.conn, DOMAIN)

        # Unchanged and still working.
        self.assert_consistent(DOMAIN, expect_public=original)
        provisioning.reconcile_dkim(self.conn)
        self.assert_consistent(DOMAIN, expect_public=original)

    def test_crash_during_same_selector_rotation_after_activation(self):
        """
        The new key is live but the row still describes the old one — the window
        that used to be documented rather than closed.

        `get_dkim_public_key` derives from the live key, so the answer is the
        NEW public key immediately, with no reconciliation needed, and the row
        is repaired as a side effect.
        """
        self.make_domain()
        original = self.assert_consistent(DOMAIN)

        self.crash_on_dkim_row_write()
        with self.assertRaises(SimulatedCrash):
            provisioning.rotate_dkim_key(self.conn, DOMAIN)

        rotated = self.assert_consistent(DOMAIN)
        self.assertNotEqual(rotated, original, "rotation did not take effect")

        provisioning.reconcile_dkim(self.conn)
        self.assert_consistent(DOMAIN, expect_public=rotated)

    def test_crash_during_rotation_after_the_row_commits(self):
        """The ordinary completed case, asserted so the sequence is covered end to end."""
        self.make_domain()
        first = self.assert_consistent(DOMAIN)
        provisioning.rotate_dkim_key(self.conn, DOMAIN)
        second = self.assert_consistent(DOMAIN)
        self.assertNotEqual(first, second)
        provisioning.reconcile_dkim(self.conn)
        self.assert_consistent(DOMAIN, expect_public=second)

    def test_rotation_with_a_new_selector_is_also_consistent(self):
        self.make_domain()
        provisioning.rotate_dkim_key(self.conn, DOMAIN, selector="rot2")
        reported = self.assert_consistent(DOMAIN)
        self.assertEqual(
            provisioning.get_dkim_public_key(self.conn, DOMAIN)["selector"], "rot2"
        )
        # NE3: the old selector's ACTIVE key is retained so an Rspamd worker still
        # holding the previous map signs correctly rather than sending unsigned.
        # Its generations are gone, the retention is bounded by a marker, and it
        # is not the selector anything publishes.
        self.assertEqual(
            sorted(self.live_files(DOMAIN, "mm1")),
            [f"{DOMAIN}.mm1.key", f"{DOMAIN}.mm1.key.retired"],
            "the outgoing key should be retained, with a marker and no generations",
        )
        self.assertEqual(engine_dkim.generations_for(DOMAIN, "mm1"), [])
        self.assertTrue(reported)

    def test_a_failed_row_update_rolls_the_activation_back(self):
        """
        An exception, unlike a crash, gets a rollback: the caller's failed
        rotation must leave the engine exactly as it found it.
        """
        self.make_domain()
        original = self.assert_consistent(DOMAIN)

        class Boom(Exception):
            pass

        self.fail_on_sql("INTO dkim_key", Boom("database write failed"))
        with self.assertRaises(Boom):
            provisioning.rotate_dkim_key(self.conn, DOMAIN)

        self.assert_consistent(DOMAIN, expect_public=original)

    # ── leftovers ───────────────────────────────────────────────────────────

    def test_no_orphan_generation_files_survive_a_completed_rotation(self):
        self.make_domain()
        for _ in range(3):
            provisioning.rotate_dkim_key(self.conn, DOMAIN)
        files = self.live_files(DOMAIN, "mm1")
        self.assertEqual(len(files), 2, f"expected the active key and one generation: {files}")
        self.assert_consistent(DOMAIN)

    def test_interrupted_temp_files_are_cleaned_up(self):
        self.make_domain()
        (self.key_dir / f".{DOMAIN}.abandoned.tmp").write_bytes(b"partial")
        (self.key_dir / f".{DOMAIN}.mm1.key.activate-deadbeef").write_bytes(b"partial")
        report = provisioning.reconcile_dkim(self.conn)
        self.assertEqual(len(report["staging_removed"]), 2, report)
        self.assert_consistent(DOMAIN)

    # ── reconciliation against deliberately inconsistent state ──────────────

    def test_reconciles_a_row_whose_metadata_is_wrong(self):
        self.make_domain()
        live = self.assert_consistent(DOMAIN)
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE dkim_key SET public_key=%s WHERE domain_name=%s",
                ("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8-THIS-IS-NOT-THE-LIVE-KEY", DOMAIN),
            )
        report = provisioning.reconcile_dkim(self.conn)
        self.assertIn(DOMAIN, report["metadata_repaired"])
        self.assert_consistent(DOMAIN, expect_public=live)

    def test_reconciles_key_files_belonging_to_no_row(self):
        self.make_domain()
        stray = engine_dkim.generate("stray.invalid", "mm1")
        engine_dkim.activate(stray["generation_path"], stray["active_path"])
        self.assertTrue(pathlib.Path(stray["active_path"]).is_file())

        report = provisioning.reconcile_dkim(self.conn)
        self.assertTrue(report["orphans_removed"])
        self.assertFalse(pathlib.Path(stray["active_path"]).is_file())
        self.assertFalse(pathlib.Path(stray["generation_path"]).is_file())
        # The legitimate domain is untouched.
        self.assert_consistent(DOMAIN)

    def test_reports_rather_than_guesses_when_a_row_has_no_key_anywhere(self):
        """
        A row with no key file cannot be repaired by minting one: the new public
        key would match no published DNS record, so mail would silently fail
        DKIM. Reconciliation reports it for an operator instead.
        """
        self.make_domain()
        selector = provisioning.get_dkim_public_key(self.conn, DOMAIN)["selector"]
        for path in self.key_dir.glob(f"{DOMAIN}.{selector}*"):
            path.unlink()

        report = provisioning.reconcile_dkim(self.conn)
        self.assertIn(DOMAIN, report["unrecoverable"])
        self.assertEqual(report["activated"], [])
        self.assert_consistent(DOMAIN, expect_none=True)
        # And it did NOT invent a key.
        self.assertEqual(self.live_files(DOMAIN, selector), [])

    def test_uses_the_generation_the_row_names_when_several_survive(self):
        """
        The row's pointer is evidence, not decoration. With two generations on
        disk and no live key, the one the row NAMES is the one that was
        committed — so it is a deduction, and refusing here would strand a
        domain that is perfectly recoverable.

        This is what separates the row-directed path from the
        only-one-survivor fallback; without it, disabling the former is
        undetectable.
        """
        self.make_domain()
        selector = provisioning.get_dkim_public_key(self.conn, DOMAIN)["selector"]
        committed_path, committed_public = self.row(
            "SELECT private_key_path, public_key FROM dkim_key WHERE domain_name=%s",
            (DOMAIN,),
        )
        # A second generation exists (an interrupted rotation), and the live
        # link is gone.
        engine_dkim.generate(DOMAIN, selector)
        engine_dkim.key_path(DOMAIN, selector).unlink()
        self.assertEqual(len(engine_dkim.generations_for(DOMAIN, selector)), 2)
        self.assertTrue(pathlib.Path(committed_path).is_file())

        report = provisioning.reconcile_dkim(self.conn)
        self.assertIn(DOMAIN, report["activated"])
        self.assertEqual(report["unrecoverable"], [])
        # It activated the COMMITTED generation, not the other one.
        self.assert_consistent(DOMAIN, expect_public=committed_public)

    def test_refuses_to_guess_when_several_generations_survive(self):
        """
        Two candidate generations and no live key: which one was published is
        unknowable from here. Activating the wrong one signs mail that fails
        DKIM, which is worse than reporting no key and letting an operator
        rotate deliberately.
        """
        self.make_domain()
        selector = provisioning.get_dkim_public_key(self.conn, DOMAIN)["selector"]
        # A second generation, plus the loss of the live link.
        extra = engine_dkim.generate(DOMAIN, selector)
        engine_dkim.key_path(DOMAIN, selector).unlink()
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE dkim_key SET private_key_path=%s WHERE domain_name=%s",
                ("/nonexistent/gone.key", DOMAIN),
            )
        self.assertGreater(len(engine_dkim.generations_for(DOMAIN, selector)), 1)

        report = provisioning.reconcile_dkim(self.conn)
        self.assertIn(DOMAIN, report["unrecoverable"])
        self.assertEqual(report["activated"], [])
        self.assert_consistent(DOMAIN, expect_none=True)
        self.assertTrue(extra)

    def test_reconciliation_is_idempotent_on_a_healthy_engine(self):
        self.make_domain()
        live = self.assert_consistent(DOMAIN)
        first = provisioning.reconcile_dkim(self.conn)
        second = provisioning.reconcile_dkim(self.conn)
        for key in ("activated", "metadata_repaired", "orphans_removed",
                    "staging_removed", "unrecoverable"):
            with self.subTest(kind=key):
                self.assertEqual(first[key], [], f"a healthy engine needed repair: {first}")
                self.assertEqual(second[key], [])
        self.assert_consistent(DOMAIN, expect_public=live)

    def test_reconciliation_never_generates_a_key(self):
        """Minting during recovery would publish material no DNS record names."""
        import inspect
        source = inspect.getsource(provisioning.reconcile_dkim)
        self.assertNotIn("generate(", source)

    # ── selector-change rotation ────────────────────────────────────────────

    def crash_on_prune(self):
        """
        Kill the process after the row is committed but before the old
        selector's files are cleaned up. Fires once, so recovery may proceed.
        """
        real = engine_dkim.prune_generations
        state = {"fired": False}

        def exploding(*a, **kw):
            if not state["fired"]:
                state["fired"] = True
                raise SimulatedCrash("process died before old-selector cleanup")
            return real(*a, **kw)

        engine_dkim.prune_generations = exploding
        self.addCleanup(lambda: setattr(engine_dkim, "prune_generations", real))

    def stored_selector(self, domain):
        return self.row("SELECT selector FROM dkim_key WHERE domain_name=%s", (domain,))[0]

    def test_selector_change_rollback_on_a_handled_exception(self):
        """
        A handled failure must be a CLEAN no-op, including across a selector
        change.

        The rollback used to restore the previous generation only when the
        selector was unchanged. On mm1 -> mm2 it removed the new generation file
        but left `<domain>.mm2.key` — a live signing key under a selector the
        database does not name, surviving a call that returned an error.
        """
        self.make_domain()
        original = self.assert_consistent(DOMAIN)
        self.assertEqual(self.stored_selector(DOMAIN), "mm1")

        class Boom(Exception):
            pass

        self.fail_on_sql("INTO dkim_key", Boom("database write failed"))
        with self.assertRaises(Boom):
            provisioning.rotate_dkim_key(self.conn, DOMAIN, selector="mm2")

        # The database never moved.
        self.assertEqual(self.stored_selector(DOMAIN), "mm1")
        # The old selector is still authoritative and unchanged.
        self.assert_consistent(DOMAIN, expect_public=original)
        # And nothing survives under the selector that failed.
        self.assertFalse(engine_dkim.key_path(DOMAIN, "mm2").exists(),
                         "a live key was left under the failed selector")
        self.assertEqual(engine_dkim.generations_for(DOMAIN, "mm2"), [],
                         "a generation was left under the failed selector")

    def test_crash_during_selector_change_before_the_row_update(self):
        """
        mm2 is live but the row still says mm1, and mm1 is ALSO still live
        because a selector change does not overwrite the old path.

        The read path follows the row, so the domain keeps signing as mm1 —
        coherent, but with a stale mm2 key on disk that nothing accounts for.
        Reconciliation must remove it and leave exactly one selector.
        """
        self.make_domain()
        original = self.assert_consistent(DOMAIN)

        self.crash_on_dkim_row_write()
        with self.assertRaises(SimulatedCrash):
            provisioning.rotate_dkim_key(self.conn, DOMAIN, selector="mm2")

        self.assertTrue(engine_dkim.key_path(DOMAIN, "mm2").exists(),
                        "precondition: the crash left an mm2 key behind")
        # Still coherent: the row and the key it names agree.
        self.assert_consistent(DOMAIN, expect_public=original)

        provisioning.reconcile_dkim(self.conn)

        self.assertEqual(self.stored_selector(DOMAIN), "mm1")
        self.assert_consistent(DOMAIN, expect_public=original)
        self.assertFalse(engine_dkim.key_path(DOMAIN, "mm2").exists(),
                         "reconciliation left the orphaned selector behind")
        self.assertEqual(engine_dkim.generations_for(DOMAIN, "mm2"), [])

    def test_crash_during_selector_change_after_the_row_update(self):
        """
        The row now says mm2 and mm2 is live, but the old mm1 files were never
        cleaned up. Reconciliation must remove them, leaving one coherent state.
        """
        self.make_domain()
        self.assert_consistent(DOMAIN)

        self.crash_on_prune()
        with self.assertRaises(SimulatedCrash):
            provisioning.rotate_dkim_key(self.conn, DOMAIN, selector="mm2")

        self.assertEqual(self.stored_selector(DOMAIN), "mm2")
        self.assertTrue(engine_dkim.key_path(DOMAIN, "mm1").exists(),
                        "precondition: the crash left the old selector behind")
        rotated = self.assert_consistent(DOMAIN)

        provisioning.reconcile_dkim(self.conn)

        # Everything agrees: row selector, live path, derived key, generations.
        self.assertEqual(self.stored_selector(DOMAIN), "mm2")
        self.assert_consistent(DOMAIN, expect_public=rotated)
        # NE3 retires rather than deletes: mm1 keeps signing for workers that
        # have not yet seen the new map, and reconciliation removes it once the
        # grace expires. The invariant that matters here is unchanged — mm1 is
        # not authoritative and cannot come back.
        self.assertTrue(engine_dkim.key_path(DOMAIN, "mm1").exists(),
                        "the superseded key must be retained, not deleted")
        self.assertTrue(engine_dkim.retirement_marker_path(DOMAIN, "mm1").is_file(),
                        "and its retention must be bounded by a marker")
        self.assertEqual(self.stored_selector(DOMAIN), "mm2")
        self.assertEqual(engine_dkim.generations_for(DOMAIN, "mm1"), [])
        self.assertEqual(len(engine_dkim.generations_for(DOMAIN, "mm2")), 1)

    # ── first creation: a row is not proof of a usable key ──────────────────

    def test_first_creation_activation_failure_then_retry_recovers(self):
        """
        A row is claimed before the key is activated, so a failure between them
        leaves a row with nothing live. A retried `ensure_domain` used to see
        the row and return success for a domain that could not sign.

        The retry must activate the generation the row already names — NOT mint
        a second identity, which would publish a key no DNS record matches.
        """
        self.crash_on_activate()
        with self.assertRaises(SimulatedCrash):
            self.make_domain()

        self.assertEqual(self.row("SELECT count(*) FROM dkim_key")[0], 1)
        recorded_public, recorded_path = self.row(
            "SELECT public_key, private_key_path FROM dkim_key WHERE domain_name=%s",
            (DOMAIN,),
        )
        self.assert_consistent(DOMAIN, expect_none=True)
        generations_before = engine_dkim.generations_for(DOMAIN, "mm1")
        self.assertEqual(len(generations_before), 1)

        # The retry MateMail would make.
        self.make_domain()

        self.assert_consistent(DOMAIN, expect_public=recorded_public)
        self.assertEqual(
            engine_dkim.generations_for(DOMAIN, "mm1"), generations_before,
            "a second DKIM identity was generated instead of recovering the first",
        )
        self.assertEqual(
            self.row("SELECT private_key_path FROM dkim_key WHERE domain_name=%s",
                     (DOMAIN,))[0],
            recorded_path,
            "the row was repointed at a different generation",
        )

    def test_a_healthy_row_is_adopted_without_touching_anything(self):
        """The ordinary idempotent path must stay a no-op."""
        self.make_domain()
        live = self.assert_consistent(DOMAIN)
        before = sorted(p.name for p in self.key_dir.glob(f"{DOMAIN}.*"))
        self.make_domain()
        self.make_domain()
        self.assertEqual(sorted(p.name for p in self.key_dir.glob(f"{DOMAIN}.*")), before)
        self.assert_consistent(DOMAIN, expect_public=live)

    def test_retry_refuses_when_recovery_would_be_a_guess(self):
        """
        A row, no live key, the recorded generation gone and two others present.
        Minting a replacement or picking one would publish material that may not
        match DNS, so `ensure_domain` must fail honestly instead.
        """
        self.crash_on_activate()
        with self.assertRaises(SimulatedCrash):
            self.make_domain()

        # Destroy the row's pointer and leave two ambiguous candidates.
        for stale in engine_dkim.generations_for(DOMAIN, "mm1"):
            stale.unlink()
        engine_dkim.generate(DOMAIN, "mm1")
        engine_dkim.generate(DOMAIN, "mm1")
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE dkim_key SET private_key_path=%s WHERE domain_name=%s",
                ("/nonexistent/gone.key", DOMAIN),
            )

        with self.assertRaises(engine_dkim.DkimError):
            self.make_domain()
        self.assert_consistent(DOMAIN, expect_none=True)

    def test_recovery_never_mints_a_replacement_identity(self):
        """
        Structural, alongside the behavioural test: the recovery branch of
        _ensure_dkim_for must activate what exists, never generate. A fresh key
        here would publish material no DNS record names.
        """
        import inspect
        # The implementation, not the lock wrapper that now fronts it.
        source = inspect.getsource(provisioning._ensure_dkim_for_unlocked)
        recovery = source[source.index("if row is not None:"):
                          source.index("material = dkim_lib.generate")]
        self.assertNotIn("generate(", recovery)
        self.assertIn("activate(", recovery)

    def test_two_concurrent_retries_after_an_activation_failure_converge(self):
        """Recovery must be safe when MateMail retries from two workers at once."""
        import threading

        self.crash_on_activate()
        with self.assertRaises(SimulatedCrash):
            self.make_domain()
        self.assert_consistent(DOMAIN, expect_none=True)
        recorded_public = self.row(
            "SELECT public_key FROM dkim_key WHERE domain_name=%s", (DOMAIN,)
        )[0]

        conns = [psycopg.connect(self.engine_dsn, autocommit=True) for _ in range(4)]
        self.addCleanup(lambda: [c.close() for c in conns])
        errors = []

        def worker(conn):
            try:
                provisioning.ensure_domain(conn, {"name": DOMAIN})
            except Exception as exc:                       # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(c,)) for c in conns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"concurrent recovery failed: {errors}")
        self.assert_consistent(DOMAIN, expect_public=recorded_public)
        self.assertEqual(len(engine_dkim.generations_for(DOMAIN, "mm1")), 1)

    # ── concurrency ─────────────────────────────────────────────────────────

    def test_a_losing_concurrent_request_cannot_destroy_the_winning_key(self):
        import threading

        conns = [psycopg.connect(self.engine_dsn, autocommit=True) for _ in range(6)]
        self.addCleanup(lambda: [c.close() for c in conns])
        errors = []

        def worker(conn):
            try:
                provisioning.ensure_domain(conn, {"name": "race.invalid"})
            except Exception as exc:                       # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(c,)) for c in conns]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"concurrent ensure_domain failed: {errors}")
        self.assertEqual(
            self.row("SELECT count(*) FROM dkim_key WHERE domain_name='race.invalid'")[0], 1
        )
        # The decisive assertion: the survivor is usable and everything agrees.
        self.assert_consistent("race.invalid")
        report = provisioning.reconcile_dkim(self.conn)
        self.assertEqual(report["unrecoverable"], [])
        self.assert_consistent("race.invalid")


class DkimLifecycleSerializationTest(EngineDatabaseTestCase):
    """
    Two DKIM lifecycle mutations for one domain must never run concurrently.

    Without serialisation two rotations interleave like this:

        A generates -> B generates -> A activates -> B activates
        -> B writes the row and returns B -> A writes the row and returns A

    A later read repairs the row, but caller A has already been handed public
    material that is not the signing key — and may already have published it.
    No subsequent repair can take that back, which is why this is a lock and not
    a reconciliation problem.
    """

    def connections(self, count):
        conns = [psycopg.connect(self.engine_dsn, autocommit=True) for _ in range(count)]
        self.addCleanup(lambda: [c.close() for c in conns])
        return conns

    def run_concurrently(self, jobs):
        """Run callables on their own threads and collect results and errors."""
        import threading

        results, errors = [], []

        def wrapper(job):
            try:
                results.append(job())
            except Exception as exc:                   # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=wrapper, args=(j,)) for j in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        for t in threads:
            self.assertFalse(t.is_alive(), "a lifecycle operation deadlocked")
        return results, errors

    def assert_coherent(self, domain):
        """Row, live key and derived public material all agree."""
        reported = provisioning.get_dkim_public_key(self.conn, domain)
        self.assertIsNotNone(reported, "no usable key survived")
        selector = reported["selector"]
        derived = engine_dkim.public_material_of(engine_dkim.key_path(domain, selector))
        self.assertEqual(reported["public_key"], derived)
        stored = self.row("SELECT public_key, selector FROM dkim_key WHERE domain_name=%s",
                          (domain,))
        self.assertEqual(stored[0], derived, "the row does not describe the live key")
        self.assertEqual(stored[1], selector)
        # The live key still has its generation file, so the pair is intact.
        token = engine_dkim.active_generation_token(domain, selector)
        self.assertIsNotNone(token, "the live key has no generation file behind it")
        return derived, selector

    # ── the entry points really are serialised ──────────────────────────────

    def test_every_lifecycle_entry_point_takes_the_lock(self):
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(provisioning))
        wrapped = {
            fn.name for fn in tree.body
            if isinstance(fn, ast.FunctionDef)
            and "dkim_lifecycle_lock" in ast.unparse(fn)
        }
        for name in ("_ensure_dkim_for", "rotate_dkim_key", "delete_dkim_key",
                     "reconcile_dkim"):
            with self.subTest(operation=name):
                self.assertIn(name, wrapped)

    def test_the_lock_is_a_database_lock_not_a_python_one(self):
        """
        A threading.Lock protects one process. The engine is expected to run
        more than one API container, and a process-local lock would protect
        nothing while looking like it does.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(engine_db.dkim_lifecycle_lock)))
        fn = tree.body[0]
        if ast.get_docstring(fn):
            # The docstring EXPLAINS why a threading.Lock is wrong, so asserting
            # on the raw source would match its own reasoning.
            fn.body = fn.body[1:]
        body = ast.unparse(fn)
        self.assertIn("pg_advisory_lock", body)
        self.assertIn("pg_advisory_unlock", body)
        self.assertNotIn("threading", body)

    def test_the_lock_is_released_after_a_crash(self):
        """
        A BaseException must not wedge the lifecycle. If the lock leaked, the
        rotation that follows would block until the test timed out.
        """
        self.make_domain()
        self.crash_on_dkim_row_write()
        with self.assertRaises(SimulatedCrash):
            provisioning.rotate_dkim_key(self.conn, DOMAIN)

        # Would hang forever if the lock were still held by this connection.
        provisioning.rotate_dkim_key(self.conn, DOMAIN)
        self.assert_coherent(DOMAIN)

    def test_the_lock_is_released_for_another_connection_after_a_crash(self):
        """The same, observed from a DIFFERENT session — a leak inside one
        connection would be invisible to the check above."""
        self.make_domain()
        self.crash_on_dkim_row_write()
        with self.assertRaises(SimulatedCrash):
            provisioning.rotate_dkim_key(self.conn, DOMAIN)

        other = self.connections(1)[0]
        results, errors = self.run_concurrently(
            [lambda: provisioning.rotate_dkim_key(other, DOMAIN)]
        )
        self.assertEqual(errors, [], f"the lock was not released: {errors}")
        self.assertEqual(len(results), 1)
        self.assert_coherent(DOMAIN)

    # ── the decisive rule ───────────────────────────────────────────────────

    def test_a_successful_response_describes_the_generation_it_committed(self):
        """
        THE decisive property. Each rotation checks, WITHIN its own lock hold,
        that the public key it is about to return is the key that is live. If
        operations could interleave, a rotation would observe another's key here
        and the assertion would fail — which is exactly the defect: a caller
        handed material that was never the signing key.

        The check runs inside the lock deliberately. Verifying after the call
        returns proves nothing, because the next rotation may legitimately have
        replaced the key by then.
        """
        self.make_domain()
        conns = self.connections(5)

        def rotate_and_check(conn):
            with engine_db.dkim_lifecycle_lock(conn):
                result = provisioning._rotate_dkim_key_unlocked(conn, DOMAIN, "")
                live = engine_dkim.public_material_of(
                    engine_dkim.key_path(DOMAIN, result["selector"])
                )
                return result["public_key"], live

        results, errors = self.run_concurrently(
            [(lambda c=c: rotate_and_check(c)) for c in conns]
        )
        self.assertEqual(errors, [], f"a serialised rotation failed: {errors}")
        self.assertEqual(len(results), len(conns))
        for returned, live in results:
            self.assertEqual(
                returned, live,
                "a rotation returned public material that was not the live key",
            )
        # Every rotation minted its own generation.
        self.assertEqual(len({r for r, _ in results}), len(conns),
                         "two rotations produced identical material")

    # ── same selector ───────────────────────────────────────────────────────

    def test_concurrent_same_selector_rotations(self):
        self.make_domain()
        conns = self.connections(5)

        results, errors = self.run_concurrently(
            [(lambda c=c: provisioning.rotate_dkim_key(c, DOMAIN, "mm1")) for c in conns]
        )
        self.assertEqual(errors, [], f"a rotation failed: {errors}")
        self.assertEqual(len(results), len(conns))

        returned = [r["public_key"] for r in results]
        self.assertEqual(len(set(returned)), len(conns),
                         "two rotations returned the same key")

        live, selector = self.assert_coherent(DOMAIN)
        self.assertEqual(selector, "mm1")
        # Exactly one of the successful responses is the surviving generation.
        self.assertEqual(sum(1 for key in returned if key == live), 1)
        # And nothing was orphaned: one generation, and it is the live one.
        generations = engine_dkim.generations_for(DOMAIN, "mm1")
        self.assertEqual(len(generations), 1, f"orphaned generations: {generations}")

    # ── different selectors ─────────────────────────────────────────────────

    def test_concurrent_different_selector_rotations(self):
        self.make_domain()
        conns = self.connections(3)
        selectors = ["mm2", "mm3", "mm4"]

        results, errors = self.run_concurrently(
            [(lambda c=c, sel=sel: provisioning.rotate_dkim_key(c, DOMAIN, sel))
             for c, sel in zip(conns, selectors)]
        )
        self.assertEqual(errors, [], f"a rotation failed: {errors}")

        live, selector = self.assert_coherent(DOMAIN)
        self.assertIn(selector, selectors)

        # Losing selectors leave no GENERATIONS and are never authoritative. Their
        # active keys may survive as RETIRED material (NE3) — that is the point of
        # retirement — but only ever with a marker bounding how long, and never as
        # the selector the engine publishes.
        for other in [s for s in selectors if s != selector] + ["mm1"]:
            with self.subTest(stale=other):
                self.assertNotEqual(other, selector)
                if engine_dkim.key_path(DOMAIN, other).exists():
                    self.assertTrue(
                        engine_dkim.retirement_marker_path(DOMAIN, other).is_file(),
                        f"{other} survived with no retirement marker, so nothing "
                        f"will ever remove it",
                    )
                self.assertEqual(engine_dkim.generations_for(DOMAIN, other), [],
                                 f"generations survived under {other}")
        self.assertEqual(len(engine_dkim.generations_for(DOMAIN, selector)), 1)
        self.assertEqual(sum(1 for r in results if r["public_key"] == live), 1)

    # ── rotation versus reconciliation ──────────────────────────────────────

    def test_rotation_versus_reconciliation(self):
        """
        Reconciliation sweeps key files for EVERY domain, so an unserialised
        sweep can delete the generation a rotation has created but not yet
        committed. Under the lock it can only ever observe a settled state.
        """
        self.make_domain()
        conns = self.connections(6)
        jobs = []
        for index, conn in enumerate(conns):
            if index % 2 == 0:
                jobs.append(lambda c=conn: provisioning.rotate_dkim_key(c, DOMAIN))
            else:
                jobs.append(lambda c=conn: provisioning.reconcile_dkim(c))

        results, errors = self.run_concurrently(jobs)
        self.assertEqual(errors, [], f"a lifecycle operation failed: {errors}")

        for result in results:
            if isinstance(result, dict) and "unrecoverable" in result:
                self.assertEqual(
                    result["unrecoverable"], [],
                    "reconciliation stranded a domain it raced with",
                )
        self.assert_coherent(DOMAIN)
        self.assertEqual(len(engine_dkim.generations_for(DOMAIN, "mm1")), 1)

    # ── rotation versus delete ──────────────────────────────────────────────

    def test_rotation_versus_delete(self):
        """
        Both orderings are valid; a mixture is not.

            delete then rotate -> a usable key exists and everything agrees
            rotate then delete -> no row, no key, no generations
        """
        self.make_domain()
        rotate_conn, delete_conn = self.connections(2)

        _, errors = self.run_concurrently([
            lambda: provisioning.rotate_dkim_key(rotate_conn, DOMAIN),
            lambda: provisioning.delete_dkim_key(delete_conn, DOMAIN),
        ])
        self.assertEqual(errors, [], f"a lifecycle operation failed: {errors}")

        rows = self.row("SELECT count(*) FROM dkim_key WHERE domain_name=%s", (DOMAIN,))[0]
        files = sorted(p.name for p in self.key_dir.glob(f"{DOMAIN}.*"))

        if rows:
            # delete, then rotate.
            self.assert_coherent(DOMAIN)
        else:
            # rotate, then delete. Nothing may be left behind.
            self.assertEqual(files, [], f"deletion left key material behind: {files}")
            self.assertIsNone(provisioning.get_dkim_public_key(self.conn, DOMAIN))

        # Either way reconciliation finds nothing to repair.
        report = provisioning.reconcile_dkim(self.conn)
        self.assertEqual(report["unrecoverable"], [], report)

    def test_delete_races_do_not_strand_partial_state(self):
        """Several concurrent deletes must converge on a clean absence."""
        self.make_domain()
        conns = self.connections(4)
        _, errors = self.run_concurrently(
            [(lambda c=c: provisioning.delete_dkim_key(c, DOMAIN)) for c in conns]
        )
        self.assertEqual(errors, [], f"a delete failed: {errors}")
        self.assertEqual(self.row("SELECT count(*) FROM dkim_key")[0], 0)
        self.assertEqual(sorted(p.name for p in self.key_dir.glob(f"{DOMAIN}.*")), [])


class NativeAdapterContractTest(AdapterContractTests, EngineDatabaseTestCase):
    """
    The SAME behavioural contract that runs against StubAdapter and
    MailcowAdapter, run against NativeMailEngineAdapter.

    This is the point of the port: MateMail's business logic should not be able
    to tell which engine is underneath. A native engine that satisfied its own
    tests but not this one would be a second engine, not a replacement.

    The adapter talks to the real request handlers over an in-process transport,
    so validation, SQL, DKIM generation and the response guard all run for real.
    """

    def setUp(self):
        super().setUp()
        self.adapter, self.session = build_native_adapter(self.conn, ENGINE_DIR)

    # ── hooks the mixin requires ────────────────────────────────────────────

    def credential_present(self, address) -> bool:
        row = self.row("SELECT password_hash FROM mailbox WHERE address=%s", (address,))
        return bool(row and row[0])

    def alias_destinations(self, address):
        """
        "What does the engine deliver for this address?"

        mailcow answers with one alias table, because that is how it stores
        both aliases and forwarding. The Native Engine keeps them apart on
        purpose — forwarding must never be reachable from the send-as query —
        so this hook consults both and reports the delivery set either way.
        The CONTRACT is about delivery; the schema underneath is the adapter's
        business.
        """
        found = provisioning.get_alias(self.conn, address)
        if found:
            return tuple(found["destinations"])
        try:
            forwarded = provisioning.get_forwarding(self.conn, address)
        except provisioning.NotFound:
            return None
        return tuple(forwarded) if forwarded else None

    # ── not applicable until NE3 / NE4 ──────────────────────────────────────
    #
    # Skipped rather than satisfied with an empty result. An adapter that
    # answered "the queue is empty" without looking would report a healthy queue
    # during an incident, and one that reported 0 MB used would be wrong the
    # moment NE3 delivers a message. The adapter raises EngineCapabilityMissing
    # for each of these, which `test_native_engine_capabilities` asserts.

    def test_get_mailbox_usage_returns_dto_or_none(self):
        self.skipTest("storage usage is measured by Dovecot — NE3")

    def test_read_methods_return_only_matemail_types(self):
        self.skipTest("exercises get_mailbox_usage, which is NE3")

    def test_queue_and_quarantine_return_lists(self):
        self.skipTest("queue and quarantine inspection is NE4")

    def test_queue_and_quarantine_actions_are_idempotent(self):
        self.skipTest("queue and quarantine actions are NE4")


class NativeAdapterCapabilityTest(EngineDatabaseTestCase):
    """The refusals are part of the contract, so they are asserted, not assumed."""

    def setUp(self):
        super().setUp()
        self.adapter, self.session = build_native_adapter(self.conn, ENGINE_DIR)

    def test_unimplemented_operations_refuse_rather_than_fabricate(self):
        from apps.mail_engine.errors import EngineCapabilityMissing
        from apps.mail_engine.dto import RateLimit

        cases = [
            ("get_mailbox_usage", lambda: self.adapter.get_mailbox_usage(ADDRESS)),
            ("get_queue_status", self.adapter.get_queue_status),
            ("get_quarantine_items", self.adapter.get_quarantine_items),
            ("cancel_queue_message", lambda: self.adapter.cancel_queue_message("x")),
            ("release_quarantine_item", lambda: self.adapter.release_quarantine_item("x")),
            ("set_mailbox_rate_limit",
             lambda: self.adapter.set_mailbox_rate_limit(ADDRESS, RateLimit(messages=10))),
            ("get_mailbox_rate_limit", lambda: self.adapter.get_mailbox_rate_limit(ADDRESS)),
            ("clear_mailbox_rate_limit", lambda: self.adapter.clear_mailbox_rate_limit(ADDRESS)),
        ]
        for name, call in cases:
            with self.subTest(operation=name):
                with self.assertRaises(EngineCapabilityMissing):
                    call()

    def test_the_implemented_method_count_is_what_the_docs_claim(self):
        """
        Counted from the code, not from prose. An earlier report said 16 of 26
        with 10 remaining — it had simply missed get_last_login and
        check_health, and nothing checked the arithmetic.
        """
        import ast
        import inspect

        from apps.mail_engine import adapter as port_module
        from apps.mail_engine import native_adapter as impl_module

        port = ast.parse(inspect.getsource(port_module))
        port_cls = next(n for n in port.body
                        if isinstance(n, ast.ClassDef) and n.name == "MailEngineAdapter")
        abstract = {
            n.name for n in port_cls.body
            if isinstance(n, ast.FunctionDef)
            and any(getattr(d, "id", "") == "abstractmethod" for d in n.decorator_list)
        }
        impl = ast.parse(inspect.getsource(impl_module))
        impl_cls = next(n for n in impl.body if isinstance(n, ast.ClassDef))
        refusing = {
            n.name for n in impl_cls.body
            if isinstance(n, ast.FunctionDef) and "_unavailable" in ast.unparse(n)
        }
        defined = {n.name for n in impl_cls.body if isinstance(n, ast.FunctionDef)}

        implemented = abstract & defined - refusing
        remaining = abstract & refusing

        self.assertEqual(len(abstract), 26, "the port's method count changed")
        self.assertEqual(len(implemented), 18, sorted(implemented))
        self.assertEqual(len(remaining), 8, sorted(remaining))
        self.assertEqual(implemented | remaining, abstract,
                         "every abstract method must be implemented or explicitly refused")

    def test_the_adapter_never_opens_a_database_connection(self):
        """
        NE0.2: matemail-native-api is the only direct writer, and Django must
        never connect to the engine database. An adapter holding a second write
        path would end that invariant on its first query.
        """
        import inspect

        from apps.mail_engine import native_adapter
        source = inspect.getsource(native_adapter)
        for forbidden in ("psycopg", "connect(", "cursor(", "SELECT ", "INSERT ", "UPDATE "):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, source)

    def test_dkim_material_that_crosses_the_boundary_carries_nothing_private(self):
        self.adapter.ensure_domain(DomainSpec(name=DOMAIN))
        info = self.adapter.rotate_dkim_key(DOMAIN)
        fields = set(vars(info).keys())
        for forbidden in ("private_key", "privkey", "private", "secret", "pem",
                          "private_key_path"):
            self.assertNotIn(forbidden, fields)
        blob = " ".join(str(v) for v in vars(info).values()).upper()
        self.assertNotIn("PRIVATE KEY", blob)

    def test_the_response_guard_refuses_private_material(self):
        """
        The backstop, exercised directly. `provisioning` does not select private
        material, but a future handler that forgot must fail loudly at the
        boundary rather than ship a key path — or a key — to MateMail.
        """
        import app as app_module

        for leak in (
            {"selector": "mm1", "private_key_path": "/var/lib/rspamd/dkim/x.key"},
            {"selector": "mm1", "password_hash": "{BLF-CRYPT}$2b$10$x"},
            {"nested": [{"secret": "x"}]},
            {"blob": "-----BEGIN RSA PRIVATE KEY-----"},
        ):
            with self.subTest(payload=sorted(leak)):
                with self.assertRaises(RuntimeError):
                    app_module._assert_response_is_safe(leak)

    def test_a_safe_response_passes_the_guard(self):
        """Otherwise the test above would pass against a guard that rejects everything."""
        import app as app_module

        app_module._assert_response_is_safe(
            {"selector": "mm1", "public_key": "MIIBIjAN", "domains": [{"name": "x.invalid"}]}
        )

    def test_an_empty_password_never_reaches_the_network(self):
        """
        Refused before the request is built. The engine would refuse it too —
        that redundancy is deliberate — but a credential that never leaves the
        process cannot be logged by anything in between.
        """
        self.adapter.ensure_domain(DomainSpec(name=DOMAIN))
        self.adapter.ensure_mailbox(
            MailboxSpec(address=ADDRESS, local_part="alice", domain=DOMAIN),
            "Initial-Passphrase-1",
        )
        sent = []
        real = self.session.request

        def recording(*args, **kwargs):
            sent.append((args, kwargs))
            return real(*args, **kwargs)

        self.session.request = recording
        try:
            from apps.mail_engine.errors import Rejected
            with self.assertRaises(Rejected):
                self.adapter.set_mailbox_password(ADDRESS, "")
        finally:
            self.session.request = real
        self.assertEqual(sent, [], "an empty password was sent to the engine")

    def test_an_empty_password_is_refused_before_it_leaves_matemail(self):
        from apps.mail_engine.errors import Rejected

        self.adapter.ensure_domain(DomainSpec(name=DOMAIN))
        self.adapter.ensure_mailbox(
            MailboxSpec(address=ADDRESS, local_part="alice", domain=DOMAIN),
            "Initial-Passphrase-1",
        )
        with self.assertRaises(Rejected):
            self.adapter.set_mailbox_password(ADDRESS, "")
