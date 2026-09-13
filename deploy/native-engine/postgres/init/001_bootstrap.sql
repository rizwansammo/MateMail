-- MateMail Native Engine — database bootstrap.
--
-- WHAT THIS CONFIGURES
--   The minimum the engine database needs to exist, report its health and
--   carry a schema version forward.
--
-- WHY IT IS SHAPED THIS WAY
--   NE1 is a foundation. Creating domain/mailbox/alias tables here would be
--   inventing NE2's schema early and making the stack *look* finished — the
--   instruction is explicit that we must not do that.
--
--   What IS created is the migration framework, because retrofitting versioning
--   onto a database that already holds mail state is painful and avoidable.
--
--   The read-only roles are created now, unprivileged and unused, so the write
--   model from NE0.2 is structurally true from the first migration rather than
--   asserted in prose: matemail-native-api is the only direct writer, and
--   Postfix and Dovecot can only ever read.
--
-- WHICH PHASE OWNS IT
--   NE1. NE2 adds the real schema on top, as migration 002+.

CREATE TABLE IF NOT EXISTS schema_version (
    version     integer     PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    description text        NOT NULL
);

INSERT INTO schema_version (version, description)
VALUES (1, 'NE1 foundation: versioning and least-privilege roles')
ON CONFLICT (version) DO NOTHING;

-- ── Least-privilege roles ────────────────────────────────────────────────────
--
-- NOLOGIN until NE3 gives them passwords and Dovecot/Postfix actually connect.
-- Creating them now means the grants are part of the schema's history rather
-- than a manual step somebody has to remember on a production host.
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

-- Read-only, and it stays that way: default privileges apply the same rule to
-- every table NE2 adds later, so a future migration cannot accidentally grant
-- Postfix or Dovecot the ability to mutate engine state.
GRANT USAGE ON SCHEMA public TO engine_ro_postfix, engine_ro_dovecot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO engine_ro_postfix, engine_ro_dovecot;
