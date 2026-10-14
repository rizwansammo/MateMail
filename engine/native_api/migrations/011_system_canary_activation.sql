-- Release 3 canary: activate ONLY operations@system.matemail.pro.
-- Never change legacy mail.matemail.pro or any tenant-owned account.
-- Preconditions: five exact inactive placeholders and a verified, existing DKIM key.
-- Other four roles stay disabled. Dedicated platform operator only.
-- Rollback is a separate fixed function; no generic mutation API is introduced.

CREATE OR REPLACE FUNCTION public.protect_platform_domain_row()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.name = 'system.matemail.pro'
       AND NEW.active IS FALSE
       AND session_user = 'engine_platform_operator'
       AND current_setting('matemail.platform_stage', true) = '1'
    THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE'
       AND OLD.name = 'system.matemail.pro'
       AND NEW.name = OLD.name
       AND session_user = 'engine_platform_operator'
       AND current_setting('matemail.platform_canary', true) = '1'
       AND (to_jsonb(NEW) - 'active' - 'updated_at') IS NOT DISTINCT FROM
           (to_jsonb(OLD) - 'active' - 'updated_at')
       AND OLD.active IS DISTINCT FROM NEW.active
    THEN
        RETURN NEW;
    END IF;
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        IF EXISTS (SELECT 1 FROM public.platform_reserved_domain WHERE name = OLD.name) THEN
            RAISE EXCEPTION 'protected platform domain cannot be modified' USING ERRCODE='42501';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        IF EXISTS (SELECT 1 FROM public.platform_reserved_domain WHERE name = NEW.name) THEN
            RAISE EXCEPTION 'protected platform domain cannot be modified' USING ERRCODE='42501';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION public.protect_platform_mailbox_row()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    old_domain text;
    new_domain text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT name INTO old_domain FROM public.domain WHERE id = OLD.domain_id;
        IF EXISTS (SELECT 1 FROM public.platform_reserved_domain WHERE name = old_domain) THEN
            IF TG_OP = 'UPDATE' THEN
                IF (to_jsonb(NEW) - 'last_login_at') IS NOT DISTINCT FROM
                   (to_jsonb(OLD) - 'last_login_at') THEN
                    RETURN NEW;
                END IF;
                IF old_domain = 'system.matemail.pro'
                   AND OLD.address = 'operations@system.matemail.pro'
                   AND NEW.address = OLD.address
                   AND session_user = 'engine_platform_operator'
                   AND current_setting('matemail.platform_canary', true) = '1'
                   AND (to_jsonb(NEW) - 'active' - 'login_enabled' - 'password_hash' - 'updated_at')
                       IS NOT DISTINCT FROM
                       (to_jsonb(OLD) - 'active' - 'login_enabled' - 'password_hash' - 'updated_at')
                   AND (
                     (OLD.active=false AND OLD.login_enabled=false AND OLD.password_hash IS NULL
                      AND NEW.active=true AND NEW.login_enabled=true
                      AND NEW.password_hash ~ '^\{BLF-CRYPT\}\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$')
                     OR
                     (OLD.active=true AND OLD.login_enabled=true
                      AND NEW.active=false AND NEW.login_enabled=false AND NEW.password_hash IS NULL)
                   )
                THEN
                    RETURN NEW;
                END IF;
            END IF;
            RAISE EXCEPTION 'protected platform mailbox cannot be modified' USING ERRCODE='42501';
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT name INTO new_domain FROM public.domain WHERE id = NEW.domain_id;
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
        IF EXISTS (SELECT 1 FROM public.platform_reserved_domain WHERE name = new_domain) THEN
            RAISE EXCEPTION 'protected platform mailbox cannot be modified' USING ERRCODE='42501';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION public.platform_activate_system_canary(canary_hash text)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
    target_id bigint;
    exact_stage boolean;
    registered_dkim boolean;
    updated_count integer;
BEGIN
    IF session_user <> 'engine_platform_operator' THEN
        RAISE EXCEPTION 'dedicated platform operator required' USING ERRCODE='42501';
    END IF;
    IF canary_hash IS NULL OR canary_hash !~
       '^\{BLF-CRYPT\}\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$'
    THEN
        RAISE EXCEPTION 'invalid canary hash' USING ERRCODE='22023';
    END IF;
    SELECT id INTO target_id FROM public.domain
     WHERE name='system.matemail.pro' AND active=false FOR UPDATE;
    IF target_id IS NULL THEN
        RAISE EXCEPTION 'target domain must be staged inactive' USING ERRCODE='42501';
    END IF;
    SELECT (COUNT(*)=5 AND COUNT(*) FILTER (
        WHERE m.local_part IN ('noreply','security','operations','dmarc','tlsrpt')
          AND m.address=m.local_part || '@system.matemail.pro'
          AND m.active=false AND m.login_enabled=false AND m.password_hash IS NULL
    )=5) INTO exact_stage
    FROM public.mailbox m WHERE m.domain_id=target_id;

    SELECT COUNT(*)=1 INTO registered_dkim FROM public.dkim_key
     WHERE domain_name='system.matemail.pro' AND selector='mm1'
       AND active=true AND key_size=2048;
    IF exact_stage IS DISTINCT FROM true OR registered_dkim IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'exact inactive stage and DKIM required' USING ERRCODE='42501';
    END IF;

    PERFORM pg_catalog.set_config('matemail.platform_canary','1',true);
    UPDATE public.domain
       SET active=true,updated_at=now()
     WHERE id=target_id AND active=false;
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count<>1 THEN RAISE EXCEPTION 'domain activation mismatch'; END IF;
    UPDATE public.mailbox
       SET active=true,login_enabled=true,password_hash=canary_hash,updated_at=now()
     WHERE domain_id=target_id AND address='operations@system.matemail.pro'
       AND active=false AND login_enabled=false AND password_hash IS NULL;
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    IF updated_count<>1 THEN RAISE EXCEPTION 'canary activation mismatch'; END IF;

    RETURN pg_catalog.jsonb_build_object(
        'domain','system.matemail.pro','canary','operations@system.matemail.pro',
        'active_mailboxes',1,'other_system_mailboxes_inactive',4,
        'legacy_modified',false,'sender_switched',false);
END
$$;
REVOKE ALL ON FUNCTION public.platform_activate_system_canary(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.platform_activate_system_canary(text)
    TO engine_platform_operator;

CREATE OR REPLACE FUNCTION public.platform_deactivate_system_canary()
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
    target_id bigint;
    exact_canary boolean;
    updated_count integer;
BEGIN
    IF session_user <> 'engine_platform_operator' THEN
        RAISE EXCEPTION 'dedicated platform operator required' USING ERRCODE='42501';
    END IF;
    SELECT id INTO target_id FROM public.domain
      WHERE name='system.matemail.pro' AND active=true FOR UPDATE;
    IF target_id IS NULL THEN
        RAISE EXCEPTION 'canary domain must be active' USING ERRCODE='42501';
    END IF;
    SELECT (COUNT(*)=5 AND COUNT(*) FILTER (
        WHERE m.address='operations@system.matemail.pro'
          AND m.local_part='operations' AND m.active=true
          AND m.login_enabled=true AND m.password_hash IS NOT NULL
    )=1 AND COUNT(*) FILTER (
        WHERE m.local_part IN ('noreply','security','dmarc','tlsrpt')
          AND m.address=m.local_part || '@system.matemail.pro'
          AND m.active=false AND m.login_enabled=false AND m.password_hash IS NULL
    )=4) INTO exact_canary FROM public.mailbox m WHERE m.domain_id=target_id;
    IF exact_canary IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'unknown canary state: operator review required' USING ERRCODE='42501';
    END IF;

    PERFORM pg_catalog.set_config('matemail.platform_canary','1',true);
    UPDATE public.mailbox
       SET active=false,login_enabled=false,password_hash=NULL,updated_at=now()
     WHERE domain_id=target_id AND address='operations@system.matemail.pro';
    GET DIAGNOSTICS updated_count=ROW_COUNT;
    IF updated_count<>1 THEN RAISE EXCEPTION 'canary deactivation mismatch'; END IF;
    UPDATE public.domain SET active=false,updated_at=now()
     WHERE id=target_id AND active=true;
    GET DIAGNOSTICS updated_count=ROW_COUNT;
    IF updated_count<>1 THEN RAISE EXCEPTION 'domain deactivation mismatch'; END IF;
    RETURN pg_catalog.jsonb_build_object(
        'domain','system.matemail.pro','state','inactive',
        'active_mailboxes',0,'legacy_modified',false,'sender_switched',false);
END
$$;
REVOKE ALL ON FUNCTION public.platform_deactivate_system_canary() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.platform_deactivate_system_canary()
    TO engine_platform_operator;
