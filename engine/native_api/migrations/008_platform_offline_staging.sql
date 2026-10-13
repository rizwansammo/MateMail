-- Phase 3: create an OFFLINE, disabled-by-default staging capability.
-- A dedicated NOLOGIN role can invoke one SECURITY DEFINER function only
-- after a separately approved credential rollout. Ordinary tenant provisioning
-- remains blocked by migration 007. No existing domain/mailbox/key is changed.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engine_platform_operator') THEN
        CREATE ROLE engine_platform_operator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
    END IF;
END
$$;
ALTER ROLE engine_platform_operator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
GRANT USAGE ON SCHEMA public TO engine_platform_operator;
REVOKE CREATE ON SCHEMA public FROM engine_platform_operator;
REVOKE ALL ON TABLE domain, mailbox, dkim_key, platform_reserved_domain FROM engine_platform_operator;

-- The session user cannot be forged by a regular unprivileged DB connection.
-- The transaction-local switch is insufficient without the dedicated role,
-- which has no direct INSERT privilege on the underlying tables.
CREATE OR REPLACE FUNCTION protect_platform_domain_row()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.name = 'system.matemail.pro'
       AND NEW.active IS FALSE
       AND session_user = 'engine_platform_operator'
       AND current_setting('matemail.platform_stage', true) = '1'
    THEN
        RETURN NEW;
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        IF EXISTS (SELECT 1 FROM platform_reserved_domain WHERE name = OLD.name) THEN
            RAISE EXCEPTION 'protected platform domain cannot be modified' USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        IF EXISTS (SELECT 1 FROM platform_reserved_domain WHERE name = NEW.name) THEN
            RAISE EXCEPTION 'protected platform domain cannot be modified' USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION protect_platform_mailbox_row()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    old_domain text;
    new_domain text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT name INTO old_domain FROM domain WHERE id = OLD.domain_id;
        IF EXISTS (SELECT 1 FROM platform_reserved_domain WHERE name = old_domain) THEN
            IF TG_OP = 'UPDATE' THEN
                IF (to_jsonb(NEW) - 'last_login_at') IS NOT DISTINCT FROM
                   (to_jsonb(OLD) - 'last_login_at') THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'protected platform mailbox cannot be modified' USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT name INTO new_domain FROM domain WHERE id = NEW.domain_id;
        IF TG_OP = 'INSERT'
           AND new_domain = 'system.matemail.pro'
           AND NEW.active IS FALSE
           AND NEW.login_enabled IS FALSE
           AND NEW.password_hash IS NULL
           AND NEW.local_part IN ('noreply','security','operations','dmarc','tlsrpt')
           AND NEW.address = NEW.local_part || '@system.matemail.pro'
           AND session_user = 'engine_platform_operator'
           AND current_setting('matemail.platform_stage', true) = '1'
        THEN
            RETURN NEW;
        END IF;
        IF EXISTS (SELECT 1 FROM platform_reserved_domain WHERE name = new_domain) THEN
            RAISE EXCEPTION 'protected platform mailbox cannot be modified' USING ERRCODE = '42501';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;

-- No payload: fixed domain and mailbox role allowlist, no caller-supplied
-- password, quota, DKIM selector, or generic domain. Retrying does not alter
-- existing rows, rotate credentials, activate delivery, or touch DKIM.
CREATE OR REPLACE FUNCTION platform_stage_system_mail()
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    target_id bigint;
    target_active boolean;
    role_name text;
    matched_address text;
    existing_domain_id bigint;
    existing_active boolean;
    existing_login_enabled boolean;
    existing_password_hash text;
    existing_local_part text;
BEGIN
    IF session_user <> 'engine_platform_operator' THEN
        RAISE EXCEPTION 'platform operation requires the dedicated database principal'
            USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.platform_reserved_domain WHERE name='system.matemail.pro') THEN
        RAISE EXCEPTION 'system domain reservation missing' USING ERRCODE = '42501';
    END IF;
    PERFORM pg_catalog.set_config('matemail.platform_stage', '1', true);
    INSERT INTO public.domain (name, active)
    VALUES ('system.matemail.pro', false)
    ON CONFLICT (name) DO NOTHING;
    SELECT id, active INTO target_id, target_active
      FROM public.domain WHERE name='system.matemail.pro' FOR UPDATE;
    IF target_id IS NULL OR target_active THEN
        RAISE EXCEPTION 'system domain is not in inactive staging state'
            USING ERRCODE = '42501';
    END IF;
    FOREACH role_name IN ARRAY ARRAY['noreply','security','operations','dmarc','tlsrpt'] LOOP
        matched_address := role_name || '@system.matemail.pro';
        INSERT INTO public.mailbox
            (domain_id,address,local_part,display_name,password_hash,quota_mb,active,login_enabled)
        VALUES (target_id, matched_address, role_name, '', null, 1024, false, false)
        ON CONFLICT (address) DO NOTHING;
        -- Re-read under lock AFTER the insert. An existing address must match
        -- every invariant, including its domain, ownership and credentials.
        -- Never silently adopt a mailbox from an unexpected existing row.
        SELECT domain_id, active, login_enabled, password_hash, local_part
          INTO existing_domain_id, existing_active, existing_login_enabled,
               existing_password_hash, existing_local_part
          FROM public.mailbox WHERE address=matched_address FOR UPDATE;
        IF NOT FOUND OR existing_domain_id IS DISTINCT FROM target_id
           OR existing_active IS DISTINCT FROM false
           OR existing_login_enabled IS DISTINCT FROM false
           OR existing_password_hash IS NOT NULL
           OR existing_local_part IS DISTINCT FROM role_name
        THEN
            RAISE EXCEPTION 'existing system mailbox state must be reviewed'
                USING ERRCODE = '42501';
        END IF;
    END LOOP;
    RETURN jsonb_build_object(
        'domain', 'system.matemail.pro',
        'state', 'inactive',
        'passwords_created', false,
        'dkim_modified', false,
        'delivery_activated', false,
        'roles', 5
    );
END;
$$;

REVOKE ALL ON FUNCTION platform_stage_system_mail() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_stage_system_mail() TO engine_platform_operator;
