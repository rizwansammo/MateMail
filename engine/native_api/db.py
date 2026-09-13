"""
Engine database access and schema migration.

WHY THE API OWNS MIGRATIONS
    `matemail-native-api` is the only component permitted to write this database
    (NE0.2), so it is the only component that can safely change its shape. A
    separate migration job would need the same write credential, which would
    mean two writers and the end of the invariant.

WHY NOT DJANGO MIGRATIONS
    This is not MateMail's database and Django must never connect to it. Plain
    versioned SQL applied by the engine keeps the two schemas genuinely separate
    rather than separate-by-convention.

CONCURRENCY
    Startup takes a PostgreSQL advisory lock before looking at the version, so
    two API containers starting together cannot both decide migration 002 is
    pending and both run it. The lock is released when the transaction ends,
    including on failure.
"""
from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import re

import psycopg

logger = logging.getLogger("matemail.native.api.db")

MIGRATIONS_DIR = pathlib.Path(__file__).with_name("migrations")

#: Arbitrary but fixed. Any advisory-lock key works as long as every API
#: instance uses the same one; this is derived from nothing so it cannot collide
#: with a key some other tool computed from a table name.
_MIGRATION_LOCK_KEY = 8451_0002

#: Serialises the DKIM lifecycle. Same reasoning, different key.
_DKIM_LIFECYCLE_LOCK_KEY = 8451_0003

#: How long a lifecycle operation will queue behind its peers before giving up.
_LOCK_WAIT_TIMEOUT = os.environ.get("NATIVE_DKIM_LOCK_TIMEOUT", "30s")

_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def dsn() -> str:
    """
    Build the connection string from the environment.

    The password is read here and never logged. `psycopg` keeps it inside the
    connection object; nothing in this module puts a DSN into a log line.
    """
    return (
        f"host={os.environ.get('NATIVE_DB_HOST', 'db')} "
        f"port={os.environ.get('NATIVE_DB_PORT', '5432')} "
        f"dbname={os.environ.get('NATIVE_DB_NAME', 'matemail_engine')} "
        f"user={os.environ.get('NATIVE_DB_USER', 'engine')} "
        f"password={os.environ.get('NATIVE_DB_PASSWORD', '')} "
        f"connect_timeout=5"
    )


def connect() -> psycopg.Connection:
    return psycopg.connect(dsn())


def current_version(conn) -> int:
    """
    Highest applied schema version, or 0 when the ladder has not started.

    A missing `schema_version` table is version 0, not an error: that is exactly
    the state of a database created outside the Docker init script, and the
    migration runner is what fixes it.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.schema_version') IS NOT NULL")
        if not cur.fetchone()[0]:
            return 0
        cur.execute("SELECT coalesce(max(version), 0) FROM schema_version")
        return int(cur.fetchone()[0])


def available_migrations() -> list[tuple[int, str, pathlib.Path]]:
    """Every shipped migration, lowest version first."""
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if not match:
            raise RuntimeError(
                f"migration {path.name!r} does not match NNN_name.sql — refusing "
                f"to guess its order"
            )
        found.append((int(match.group(1)), path.stem, path))
    versions = [v for v, _, _ in found]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"duplicate migration versions: {versions}")
    return found


def apply_migrations(conn) -> int:
    """
    Bring the database up to the newest shipped version. Returns that version.

    Each migration runs in its own transaction together with the
    `schema_version` row that records it, so a failure leaves neither the change
    nor the claim that it was applied. Partially-applied schema with a version
    that says otherwise is the failure mode this avoids.
    """
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
        start = current_version(conn)

    applied = start
    for version, name, path in available_migrations():
        if version <= start:
            continue
        sql = path.read_text(encoding="utf-8")
        logger.info("applying engine migration %03d (%s)", version, name)
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
                # Re-read under the lock: another instance may have applied this
                # migration between our check and our turn.
                if current_version(conn) >= version:
                    logger.info("migration %03d already applied elsewhere", version)
                    continue
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_version (version, description) "
                    "VALUES (%s, %s) ON CONFLICT (version) DO NOTHING",
                    (version, name),
                )
        applied = version

    if applied != start:
        logger.info("engine schema now at version %d (was %d)", applied, start)
    return applied


#: The schema version this build of the API requires. Readiness reports a
#: mismatch rather than serving requests against a schema it was not written
#: for — a newer API against an older database is how half-written rows happen.
REQUIRED_VERSION = 2


@contextlib.contextmanager
def dkim_lifecycle_lock(conn):
    """
    Serialise the DKIM lifecycle across threads, processes and containers.

    WHY A DATABASE LOCK AND NOT A PYTHON ONE
        A `threading.Lock` protects one process. The engine runs one API
        container today and is expected to run more; the moment it does, a
        process-local lock protects nothing while looking like it does.

    WHY IT IS GLOBAL RATHER THAN PER DOMAIN
        Per-domain would be finer, and would still be wrong: reconciliation
        sweeps key files belonging to EVERY domain, so it would have to hold
        every domain's lock to be safe against an in-flight rotation elsewhere.
        These operations are rare — a domain is created once and rotated
        occasionally — so one lock is both simpler and sound. Provisioning
        mailboxes, aliases and forwarding is unaffected.

    WHY SESSION-SCOPED RATHER THAN TRANSACTION-SCOPED
        `pg_advisory_xact_lock` would be tidier to release, but it would also
        force every write in the lifecycle into one transaction that commits
        only at the end of the block. That silently changes what a crash leaves
        behind — the row update would no longer be durable before the cleanup
        that follows it — and the crash-consistency tests are written against
        the commit points as they are. Serialisation should not quietly rewrite
        the durability model it is protecting.

    RELEASE ON EVERY PATH
        normal success and handled exceptions   the `finally` below
        BaseException (including test crashes)  the `finally` below
        process death / connection loss         PostgreSQL releases every
                                                session lock when the backend
                                                exits, so a killed container
                                                cannot wedge the next rotation
    """
    with conn.cursor() as cur:
        # Wait, but not forever. A lifecycle operation is rare and short, so a
        # wait this long means something is wrong — a wedged peer, or a lock
        # that leaked — and an operation that blocks indefinitely is harder to
        # diagnose than one that fails saying why. Generous enough that real
        # contention (several queued rotations, each a couple of hundred
        # milliseconds of RSA keygen) never trips it.
        # `SET` takes no bound parameter, and interpolating one into SQL is not
        # a habit worth starting; `set_config` is the parameterised form.
        cur.execute("SELECT set_config('lock_timeout', %s, false)",
                    (_LOCK_WAIT_TIMEOUT,))
        try:
            cur.execute("SELECT pg_advisory_lock(%s)", (_DKIM_LIFECYCLE_LOCK_KEY,))
        except psycopg.errors.LockNotAvailable as exc:
            raise RuntimeError(
                "timed out waiting for the DKIM lifecycle lock; another operation "
                "is holding it far longer than one should"
            ) from exc
        finally:
            cur.execute("SELECT set_config('lock_timeout', '0', false)")
    try:
        yield
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_DKIM_LIFECYCLE_LOCK_KEY,))
        except Exception as exc:                 # noqa: BLE001
            # The connection is already unusable, which means the backend is on
            # its way out and PostgreSQL will drop the lock with it. Nothing to
            # repair, but worth saying so rather than passing silently.
            logger.warning(
                "could not release the DKIM lifecycle lock (%s); PostgreSQL will "
                "drop it when this connection closes", type(exc).__name__,
            )
