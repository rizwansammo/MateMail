-- Phase 7A: password-dependent Dovecot SQL authentication.
-- This migration is additive; the existing dovecot_auth view and Dovecot
-- passdb remain unchanged until a separately authorized full cutover.
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

-- A true bcrypt match is required before a hash may be returned to Dovecot.
-- pgcrypto uses the $2a$ identifier; native Python bcrypt hashes use $2b$.
-- For <=72-byte passwords the bcrypt payload is compatible after normalizing
-- the identifier. Compare the bcrypt payload, never the different prefix.
CREATE FUNCTION public.mail_client_verify_bcrypt(raw text, scheme_hash text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    bcrypt_hash text;
    normalized text;
BEGIN
    IF raw IS NULL OR length(raw) = 0 OR octet_length(raw) > 72
       OR scheme_hash IS NULL THEN
        RETURN false;
    END IF;
    IF scheme_hash !~ '^\{BLF-CRYPT\}\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$' THEN
        RETURN false;
    END IF;
    bcrypt_hash := substring(scheme_hash FROM 12);
    normalized := overlay(bcrypt_hash PLACING 'a' FROM 3 FOR 1);
    RETURN substring(public.crypt(raw, normalized) FROM 5) =
           substring(normalized FROM 5);
EXCEPTION WHEN others THEN
    RETURN false;
END
$$;

-- Dovecot must receive exactly one matching scheme-prefixed hash, not
-- multiple candidate rows. The final bcrypt verification remains with Dovecot.
-- Legacy policy row absence preserves credentials until controlled migration.
CREATE FUNCTION public.dovecot_client_auth(candidate_address text, raw text)
RETURNS TABLE(address text, password_hash text)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    target_id bigint;
    target_address text;
    primary_hash text;
    permitted boolean;
    dedicated boolean;
BEGIN
    IF raw IS NULL OR length(raw) = 0 OR octet_length(raw) > 72 THEN
        RETURN;
    END IF;

    SELECT m.id, m.address, m.password_hash,
           coalesce(p.client_access_enabled, true),
           coalesce(p.dedicated_password_required, false)
      INTO target_id, target_address, primary_hash, permitted, dedicated
      FROM public.mailbox m
      JOIN public.domain d ON d.id = m.domain_id
      LEFT JOIN public.mail_client_policy p ON p.mailbox_id = m.id
     WHERE m.address = lower(candidate_address)
       AND m.active AND m.login_enabled AND d.active;

    IF target_id IS NULL OR NOT permitted THEN
        RETURN;
    END IF;

    IF NOT dedicated AND public.mail_client_verify_bcrypt(raw, primary_hash) THEN
        address := target_address;
        password_hash := primary_hash;
        RETURN NEXT;
        RETURN;
    END IF;

    SELECT c.password_hash INTO password_hash
      FROM public.mail_client_password c
     WHERE c.mailbox_id = target_id
       AND c.revoked_at IS NULL
       AND public.mail_client_verify_bcrypt(raw, c.password_hash)
     ORDER BY c.created_at DESC, c.id
     LIMIT 1;

    IF password_hash IS NOT NULL THEN
        address := target_address;
        RETURN NEXT;
    END IF;
END
$$;

REVOKE ALL ON FUNCTION public.mail_client_verify_bcrypt(text, text)
  FROM PUBLIC, engine_ro_postfix, engine_ro_dovecot;
REVOKE ALL ON FUNCTION public.dovecot_client_auth(text, text)
  FROM PUBLIC, engine_ro_postfix, engine_ro_dovecot;
GRANT EXECUTE ON FUNCTION public.dovecot_client_auth(text, text)
  TO engine_ro_dovecot;

-- The Native runtime is the only application writer; bootstrap owner retains
-- migration privileges. Read-only mail daemons receive no table grants.
GRANT SELECT, INSERT, UPDATE, DELETE ON
    public.mail_client_policy, public.mail_client_password
  TO engine_api_runtime;

-- Nothing grants Dovecot direct access to secret tables or to the primary
-- verifier. This is an authenticated oracle with existing Dovecot throttling.
