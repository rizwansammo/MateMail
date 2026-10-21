"""Apply the REAL Native SQL migrations to a disposable PostgreSQL database.

Only invoked by CI against its temporary postgres:16 service. Never use with
MateServer or a production DB; no credentials or secret hashes are printed.
"""
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import os
import uuid

import psycopg


def assert_rows(conn, address, password, expected):
    got = conn.execute(
        "SELECT password_hash FROM public.dovecot_client_auth(%s, %s)",
        (address, password),
    ).fetchall()
    assert len(got) == expected, (address, "expected", expected, "received", len(got))


def main():
    base = os.environ.get("DATABASE_URL", "")
    # Refuse to run against any non-CI database or remote address.
    parts = urlsplit(base)
    assert parts.hostname in {"localhost", "127.0.0.1"}, "Local CI database required"
    assert parts.path == "/matemail", "Expected the disposable CI service"
    assert parts.username == "matemail", "Expected synthetic CI-only superuser"
    test_db = "native_mfa_sql_ci"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute("CREATE DATABASE " + test_db)

    target = urlunsplit((parts.scheme, parts.netloc, "/" + test_db, parts.query, parts.fragment))
    migration_dir = Path(__file__).resolve().parents[1] / "migrations"
    with psycopg.connect(target) as conn:
        for migration in sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql")):
            with conn.transaction():
                conn.execute(migration.read_text())

        owner_a, owner_b = str(uuid.uuid4()), str(uuid.uuid4())
        def create_domain(name, owner):
            return conn.execute(
                "INSERT INTO public.domain (name, tenant_id) VALUES (%s, %s::uuid) RETURNING id",
                (name, owner),
            ).fetchone()[0]

        first = create_domain("alpha-ci.example", owner_a)
        other = create_domain("beta-ci.example", owner_b)
        primary = "{BLF-CRYPT}" + conn.execute(
            "SELECT public.crypt(%s, public.gen_salt('bf', 4))", ("test-primary-123",)
        ).fetchone()[0]
        app = "{BLF-CRYPT}" + conn.execute(
            "SELECT public.crypt(%s, public.gen_salt('bf', 4))", ("test-app-456",)
        ).fetchone()[0]
        def create_mailbox(domain_id, address):
            return conn.execute(
                """INSERT INTO public.mailbox
                   (domain_id, address, local_part, password_hash, login_enabled)
                   VALUES (%s, %s, %s, %s, true) RETURNING id""",
                (domain_id, address, address.split("@")[0], primary),
            ).fetchone()[0]

        mfa_user = create_mailbox(first, "alice@alpha-ci.example")
        create_mailbox(other, "staff@beta-ci.example")
        assert_rows(conn, "alice@alpha-ci.example", "test-primary-123", 1)
        assert_rows(conn, "staff@beta-ci.example", "test-primary-123", 1)

        conn.execute(
            "INSERT INTO public.native_tenant_mfa_policy (tenant_id, required) VALUES (%s::uuid, true)",
            (owner_a,),
        )
        # Existing mailbox cannot authenticate to IMAP/SMTP with primary.
        assert_rows(conn, "alice@alpha-ci.example", "test-primary-123", 0)
        # A separate tenant is unaffected.
        assert_rows(conn, "staff@beta-ci.example", "test-primary-123", 1)
        # New mailbox on an already-bound Required domain is denied before setup.
        create_mailbox(first, "new@alpha-ci.example")
        assert_rows(conn, "new@alpha-ci.example", "test-primary-123", 0)

        conn.execute(
            """INSERT INTO public.mail_client_password
               (mailbox_id, label, password_hash) VALUES (%s, 'CI Client', %s)""",
            (mfa_user, app),
        )
        conn.execute(
            """INSERT INTO public.mail_client_policy
               (mailbox_id, client_access_enabled, dedicated_password_required)
               VALUES (%s, true, true)""",
            (mfa_user,),
        )
        # After enrollment a dedicated password may authenticate, primary never.
        assert_rows(conn, "alice@alpha-ci.example", "test-primary-123", 0)
        assert_rows(conn, "alice@alpha-ci.example", "test-app-456", 1)
        conn.execute(
            "UPDATE public.mail_client_password SET revoked_at=now() WHERE mailbox_id=%s",
            (mfa_user,),
        )
        assert_rows(conn, "alice@alpha-ci.example", "test-app-456", 0)
        conn.execute(
            "UPDATE public.native_tenant_mfa_policy SET required=false WHERE tenant_id=%s::uuid",
            (owner_a,),
        )
        # Disabling the org requirement does not undo a voluntary 2FA enrollment.
        assert_rows(conn, "alice@alpha-ci.example", "test-primary-123", 0)
        # Unenrolled mailboxes return to their legacy primary auth.
        assert_rows(conn, "new@alpha-ci.example", "test-primary-123", 1)
        conn.rollback()
    print("NATIVE_TENANT_MFA_SQL_GATE: PASS (existing, future, cross-tenant, revoke and rollback)")


if __name__ == "__main__":
    main()
