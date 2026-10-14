-- Fixed platform domain remaining roles only.
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
                IF old_domain='system.matemail.pro'
                   AND OLD.local_part IN ('noreply','security','dmarc','tlsrpt')
                   AND OLD.address=OLD.local_part || '@system.matemail.pro'
                   AND NEW.address=OLD.address
                   AND session_user='engine_platform_operator'
                   AND current_setting('matemail.platform_remaining',true)='1'
                   AND (to_jsonb(NEW)-'active'-'login_enabled'-'password_hash'-'updated_at')
                      IS NOT DISTINCT FROM
                       (to_jsonb(OLD)-'active'-'login_enabled'-'password_hash'-'updated_at')
                   AND (
                       (OLD.active=false AND OLD.login_enabled=false AND OLD.password_hash IS NULL
                        AND NEW.active=true AND NEW.login_enabled=true
                        AND NEW.password_hash ~ '^\{BLF-CRYPT\}\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$')
                       OR
                       (OLD.active=true AND OLD.login_enabled=true AND OLD.password_hash IS NOT NULL
                        AND NEW.active=false AND NEW.login_enabled=false AND NEW.password_hash IS NULL)
                   )
                THEN RETURN NEW; END IF;
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

CREATE OR REPLACE FUNCTION public.platform_activate_remaining_system_roles(
  noreply_hash text, security_hash text, dmarc_hash text, tlsrpt_hash text
)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  target bigint;
  eligible boolean;
  role_name text;
  selected_hash text;
  changed integer;
BEGIN
  IF session_user <> 'engine_platform_operator' THEN
    RAISE EXCEPTION 'dedicated operator required' USING ERRCODE='42501';
  END IF;
  FOREACH selected_hash IN ARRAY ARRAY[noreply_hash,security_hash,dmarc_hash,tlsrpt_hash] LOOP
    IF selected_hash IS NULL OR selected_hash !~ '^\{BLF-CRYPT\}\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$' THEN
      RAISE EXCEPTION 'invalid system credential hash' USING ERRCODE='22023';
    END IF;
  END LOOP;
  SELECT id INTO target FROM public.domain
    WHERE name='system.matemail.pro' AND active=true FOR UPDATE;
  IF target IS NULL THEN
    RAISE EXCEPTION 'verified operations canary is required' USING ERRCODE='42501';
  END IF;
  SELECT (count(*)=5
    AND count(*) FILTER (WHERE m.local_part='operations'
      AND m.address='operations@system.matemail.pro'
      AND m.active=true AND m.login_enabled=true AND m.password_hash IS NOT NULL)=1
    AND count(*) FILTER (WHERE m.local_part IN ('noreply','security','dmarc','tlsrpt')
      AND m.address=m.local_part || '@system.matemail.pro'
      AND m.active=false AND m.login_enabled=false AND m.password_hash IS NULL)=4)
   INTO eligible FROM public.mailbox m WHERE m.domain_id=target;
  IF eligible IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'unexpected canary stage' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.dkim_key WHERE domain_name='system.matemail.pro'
      AND selector='mm1' AND active=true
  ) THEN
    RAISE EXCEPTION 'missing system DKIM' USING ERRCODE='42501';
  END IF;
  PERFORM pg_catalog.set_config('matemail.platform_remaining','1',true);
  FOREACH role_name IN ARRAY ARRAY['noreply','security','dmarc','tlsrpt'] LOOP
    selected_hash:=CASE role_name WHEN 'noreply' THEN noreply_hash
      WHEN 'security' THEN security_hash WHEN 'dmarc' THEN dmarc_hash
      WHEN 'tlsrpt' THEN tlsrpt_hash END;
    UPDATE public.mailbox SET active=true,login_enabled=true,password_hash=selected_hash,updated_at=now()
    WHERE domain_id=target AND address=role_name || '@system.matemail.pro'
      AND active=false AND login_enabled=false AND password_hash IS NULL;
    GET DIAGNOSTICS changed=ROW_COUNT;
    IF changed<>1 THEN RAISE EXCEPTION 'role activation mismatch'; END IF;
  END LOOP;
  RETURN pg_catalog.jsonb_build_object('domain','system.matemail.pro','active_mailboxes',5,
     'legacy_modified',false,'sender_switched',false);
END
$$;
REVOKE ALL ON FUNCTION public.platform_activate_remaining_system_roles(text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.platform_activate_remaining_system_roles(text,text,text,text)
  TO engine_platform_operator;

CREATE OR REPLACE FUNCTION public.platform_deactivate_remaining_system_roles()
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
  target bigint;
  eligible boolean;
  role_name text;
  changed integer;
BEGIN
  IF session_user <> 'engine_platform_operator' THEN
    RAISE EXCEPTION 'dedicated operator required' USING ERRCODE='42501';
  END IF;
  SELECT id INTO target FROM public.domain
    WHERE name='system.matemail.pro' AND active=true FOR UPDATE;
  IF target IS NULL THEN
    RAISE EXCEPTION 'active canary domain required' USING ERRCODE='42501';
  END IF;
  SELECT (count(*)=5 AND count(*) FILTER (
      WHERE m.active=true AND m.login_enabled=true AND m.password_hash IS NOT NULL
        AND m.address=m.local_part || '@system.matemail.pro'
        AND m.local_part IN ('operations','noreply','security','dmarc','tlsrpt')
    )=5) INTO eligible FROM public.mailbox m WHERE m.domain_id=target;
  IF eligible IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'unexpected role state' USING ERRCODE='42501';
  END IF;
  PERFORM pg_catalog.set_config('matemail.platform_remaining','1',true);
  FOREACH role_name IN ARRAY ARRAY['noreply','security','dmarc','tlsrpt'] LOOP
    UPDATE public.mailbox SET active=false,login_enabled=false,
      password_hash=NULL,updated_at=now()
    WHERE domain_id=target AND address=role_name || '@system.matemail.pro'
      AND active=true AND login_enabled=true AND password_hash IS NOT NULL;
    GET DIAGNOSTICS changed=ROW_COUNT;
    IF changed<>1 THEN RAISE EXCEPTION 'role rollback mismatch'; END IF;
  END LOOP;
  RETURN pg_catalog.jsonb_build_object('domain','system.matemail.pro','active_mailboxes',1,
    'legacy_modified',false,'sender_switched',false);
END
$$;
REVOKE ALL ON FUNCTION public.platform_deactivate_remaining_system_roles() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.platform_deactivate_remaining_system_roles()
 TO engine_platform_operator;
