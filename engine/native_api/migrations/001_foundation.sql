-- MateMail Native Engine — foundation (migration form of the NE1 bootstrap).
--
-- WHY THIS EXISTS TWICE
--   `deploy/native-engine/postgres/init/001_bootstrap.sql` is byte-for-byte the
--   same intent, but PostgreSQL runs `/docker-entrypoint-initdb.d` ONLY when the
--   data directory is empty. That covers a brand-new deployment and nothing
--   else: it does not run for a database created some other way, and it cannot
--   be used to test the upgrade path.
--
--   So the API owns the migration ladder, starting at rung one. On the deployed
--   MateServer database — which already carries version 1 from the init script —
--   this is a no-op and the runner moves straight to 002. On a bare database it
--   builds the same foundation first. Both paths converge, and both are tested.
--
--   Every statement is idempotent for exactly that reason.
--
-- WHICH PHASE OWNS IT
--   NE1 created the state; NE2 made it reachable as a migration.

CREATE TABLE IF NOT EXISTS schema_version (
    version     integer     PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    description text        NOT NULL
);

INSERT INTO schema_version (version, description)
VALUES (1, 'NE1 foundation: versioning and least-privilege roles')
ON CONFLICT (version) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engine_ro_postfix') THEN
        CREATE ROLE engine_ro_postfix NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engine_ro_dovecot') THEN
        CREATE ROLE engine_ro_dovecot NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO engine_ro_postfix, engine_ro_dovecot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO engine_ro_postfix, engine_ro_dovecot;
