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
VALUES ('mail.matemail.pro'), ('system.matemail.pro')
ON CONFLICT (name) DO NOTHING;

-- Database enforcement, even if a future API handler forgets to call its
-- application guard. Mailbox login activity remains allowed, but not any
-- mutation of identity, credentials, quota, login status or active state.
CREATE OR REPLACE FUNCTION protect_platform_domain_row()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') AND EXISTS (
        SELECT 1 FROM platform_reserved_domain WHERE name = OLD.name
    ) THEN
        RAISE EXCEPTION 'protected platform domain cannot be modified'
            USING ERRCODE = '42501';
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') AND EXISTS (
        SELECT 1 FROM platform_reserved_domain WHERE name = NEW.name
    ) THEN
        RAISE EXCEPTION 'protected platform domain cannot be modified'
            USING ERRCODE = '42501';
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
    -- Dovecot last-login bookkeeping is not provisioning or a privilege
    -- change. Do not interrupt legitimate system mailbox authentication.
    IF TG_OP = 'UPDATE'
       AND (OLD.domain_id, OLD.address, OLD.local_part, OLD.display_name,
            OLD.password_hash, OLD.quota_mb, OLD.active, OLD.login_enabled)
       IS NOT DISTINCT FROM
           (NEW.domain_id, NEW.address, NEW.local_part, NEW.display_name,
            NEW.password_hash, NEW.quota_mb, NEW.active, NEW.login_enabled)
    THEN
        RETURN NEW;
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT name INTO old_domain FROM domain WHERE id = OLD.domain_id;
        IF EXISTS (SELECT 1 FROM platform_reserved_domain WHERE name = old_domain) THEN
            RAISE EXCEPTION 'protected platform mailbox cannot be modified'
                USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT name INTO new_domain FROM domain WHERE id = NEW.domain_id;
        IF EXISTS (SELECT 1 FROM platform_reserved_domain WHERE name = new_domain) THEN
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
