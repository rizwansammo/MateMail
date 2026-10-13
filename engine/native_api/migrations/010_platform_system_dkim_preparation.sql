-- Release 3: allow only the dedicated platform operator to prepare a DKIM
-- key for the inactive, fixed system domain. Existing domains, passwords,
-- keys, routing and mailboxes are not updated. No activation occurs here.
--
-- SECURITY DEFINER is deliberately limited to exact, checked parameters.
-- Ordinary Native API and Dovecot/Postfix roles have no EXECUTE permission.

CREATE OR REPLACE FUNCTION public.platform_system_dkim_state()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    entry record;
BEGIN
    IF session_user <> 'engine_platform_operator' THEN
        RAISE EXCEPTION 'platform operation requires dedicated operator'
            USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.domain
         WHERE name = 'system.matemail.pro' AND active = false
    ) THEN
        RAISE EXCEPTION 'system domain must remain inactive' USING ERRCODE='42501';
    END IF;
    IF (
        SELECT COUNT(*) FROM public.mailbox m
        JOIN public.domain d ON d.id = m.domain_id
        WHERE d.name = 'system.matemail.pro'
          AND m.local_part IN ('noreply','security','operations','dmarc','tlsrpt')
          AND m.address = m.local_part || '@system.matemail.pro'
          AND m.active = false AND m.login_enabled = false
          AND m.password_hash IS NULL
    ) <> 5 THEN
        RAISE EXCEPTION 'five inactive system identities required' USING ERRCODE='42501';
    END IF;
    SELECT selector, public_key, private_key_path, active
      INTO entry
      FROM public.dkim_key
     WHERE domain_name = 'system.matemail.pro';
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    RETURN pg_catalog.jsonb_build_object(
        'selector', entry.selector,
        'public_key', entry.public_key,
        'generation_path', entry.private_key_path,
        'active', entry.active
    );
END
$$;
REVOKE ALL ON FUNCTION public.platform_system_dkim_state() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.platform_system_dkim_state() TO engine_platform_operator;

CREATE OR REPLACE FUNCTION public.platform_register_system_dkim(
    candidate_public_key text,
    candidate_generation_path text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    target_domain_id bigint;
    staged boolean;
    existing record;
BEGIN
    IF session_user <> 'engine_platform_operator' THEN
        RAISE EXCEPTION 'platform operation requires dedicated operator'
            USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.platform_reserved_domain
        WHERE name = 'system.matemail.pro'
    ) THEN
        RAISE EXCEPTION 'missing system domain reservation' USING ERRCODE='42501';
    END IF;

    SELECT id INTO target_domain_id FROM public.domain
      WHERE name = 'system.matemail.pro' AND active = false
      FOR UPDATE;
    IF target_domain_id IS NULL THEN
        RAISE EXCEPTION 'system domain must be staged and inactive'
            USING ERRCODE='42501';
    END IF;
    SELECT (
       COUNT(*) = 5
       AND COUNT(*) FILTER (
          WHERE m.local_part IN ('noreply','security','operations','dmarc','tlsrpt')
            AND m.address = m.local_part || '@system.matemail.pro'
            AND m.active = false AND m.login_enabled = false
            AND m.password_hash IS NULL
       ) = 5
    ) INTO staged
      FROM public.mailbox AS m
      JOIN public.domain AS d ON d.id = m.domain_id
     WHERE d.name = 'system.matemail.pro' AND d.id = target_domain_id;

    IF staged IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'five exact inactive system roles are required'
            USING ERRCODE='42501';
    END IF;

    IF candidate_public_key IS NULL
       OR length(candidate_public_key) < 300
       OR length(candidate_public_key) > 800
       OR candidate_public_key !~ '^[A-Za-z0-9+/=]+$'
       OR candidate_generation_path IS NULL
       OR candidate_generation_path !~
          '^/var/lib/rspamd/dkim/system[.]matemail[.]pro[.]mm1[.]g[0-9a-f]{16}[.]key$'
    THEN
        RAISE EXCEPTION 'invalid DKIM registration metadata'
            USING ERRCODE='22023';
    END IF;

    SELECT public_key, private_key_path, selector, active
      INTO existing
      FROM public.dkim_key
     WHERE domain_name = 'system.matemail.pro' FOR UPDATE;

    IF FOUND THEN
        IF existing.public_key = candidate_public_key
           AND existing.private_key_path = candidate_generation_path
           AND existing.selector = 'mm1'
           AND existing.active = true
        THEN
            RETURN true; -- idempotent exact retry
        END IF;
        RAISE EXCEPTION 'system DKIM already exists; no implicit rotation'
            USING ERRCODE='42501';
    END IF;

    INSERT INTO public.dkim_key
        (domain_name, selector, public_key, private_key_path, key_size, active)
    VALUES
        ('system.matemail.pro','mm1',
         candidate_public_key,candidate_generation_path,2048,true);
    RETURN true;
END
$$;
REVOKE ALL ON FUNCTION public.platform_register_system_dkim(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.platform_register_system_dkim(text,text)
    TO engine_platform_operator;
