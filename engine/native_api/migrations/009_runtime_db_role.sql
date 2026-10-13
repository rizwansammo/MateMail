-- Phase 3: least-privilege identity for regular Native API request handling.
-- New role is NOLOGIN by default; only startup migration/credential rotation
-- retains the separately configured bootstrap DB account.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='engine_api_runtime') THEN
        CREATE ROLE engine_api_runtime
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;
ALTER ROLE engine_api_runtime
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

DO $runtime_grant$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO engine_api_runtime', current_database());
END
$runtime_grant$;
GRANT USAGE ON SCHEMA public TO engine_api_runtime;
REVOKE CREATE ON SCHEMA public FROM engine_api_runtime;

-- Read API projections; the dedicated role does not need to own schema,
-- functions, triggers, tables, replication or any reader credential.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO engine_api_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO engine_api_runtime;

GRANT INSERT, UPDATE, DELETE ON
    domain, mailbox, alias, alias_destination, forwarding, dkim_key,
    retired_mailbox_storage, mailbox_rate_limit,
    mailbox_sender_authorization, forward_group,
    forward_group_destination, forward_group_sender
TO engine_api_runtime;

-- Stage command belongs to a wholly separate, platform-only principal.
REVOKE ALL ON FUNCTION platform_stage_system_mail() FROM engine_api_runtime;
