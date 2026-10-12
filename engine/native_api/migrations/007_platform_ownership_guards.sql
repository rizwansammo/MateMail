-- Phase 2: immutable platform domain reservations for the Native Engine.
-- Existing data, passwords, mail storage, DKIM and active transport are untouched.
-- Until an audited platform management endpoint exists, ordinary Native API
-- mutation calls must NEVER change these identities.
CREATE TABLE IF NOT EXISTS platform_reserved_domain (
    name text PRIMARY KEY,
    registered_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT platform_reserved_domain_normalized CHECK (name = lower(name))
);
INSERT INTO platform_reserved_domain (name)
VALUES ('mail.matemail.pro'), ('system.matemail.pro'), ('mx.matemail.pro'), ('platform.matemail.pro'), ('portal.matemail.pro'), ('postbox.matemail.pro'), ('custom.matemail.pro'), ('autodiscover.matemail.pro'), ('admin.matemail.pro'), ('www.matemail.pro')
ON CONFLICT (name) DO NOTHING;

-- Database enforcement, even if a future API handler forgets to call its
-- application guard. Mailbox login activity remains allowed, but not any
-- mutation of identity, credentials, quota, login status or active state.
CREATE OR REPLACE FUNCTION protect_platform_domain_row()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Branch on operation before reading OLD or NEW. INSERT has no OLD row
    -- and DELETE has no NEW row.
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        IF EXISTS (
            SELECT 1 FROM platform_reserved_domain WHERE name = OLD.name
        ) THEN
            RAISE EXCEPTION 'protected platform domain cannot be modified'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        IF EXISTS (
            SELECT 1 FROM platform_reserved_domain WHERE name = NEW.name
        ) THEN
            RAISE EXCEPTION 'protected platform domain cannot be modified'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER platform_domain_write_guard
BEFORE INSERT OR UPDATE OR DELETE ON domain
FOR EACH ROW EXECUTE FUNCTION protect_platform_domain_row();

CREATE OR REPLACE FUNCTION protect_platform_mailbox_row()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    old_domain text;
    new_domain text;
BEGIN
    -- Native Dovecot login auditing only writes last_login_at. Allow exactly
    -- that field to change without opening a storage_id, password, quota,
    -- forwarding or sender-authorisation privilege escalation route.
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT name INTO old_domain FROM domain WHERE id = OLD.domain_id;
        IF EXISTS (
            SELECT 1 FROM platform_reserved_domain WHERE name = old_domain
        ) THEN
            IF TG_OP = 'UPDATE' THEN
                IF (to_jsonb(NEW) - 'last_login_at')
                    IS NOT DISTINCT FROM
                    (to_jsonb(OLD) - 'last_login_at') THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'protected platform mailbox cannot be modified'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT name INTO new_domain FROM domain WHERE id = NEW.domain_id;
        IF EXISTS (
            SELECT 1 FROM platform_reserved_domain WHERE name = new_domain
        ) THEN
            RAISE EXCEPTION 'protected platform mailbox cannot be modified'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER platform_mailbox_write_guard
BEFORE INSERT OR UPDATE OR DELETE ON mailbox
FOR EACH ROW EXECUTE FUNCTION protect_platform_mailbox_row();

REVOKE ALL ON platform_reserved_domain FROM PUBLIC;
