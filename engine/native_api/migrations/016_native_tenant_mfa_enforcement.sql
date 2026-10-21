-- Tenant-wide MFA enforcement with Native/Dovecot as the protocol boundary.
-- This migration is additive and defaults to NO tenant enforcement.
-- The internal API must bind each tenant's domains before Required can activate.
ALTER TABLE public.domain ADD COLUMN tenant_id uuid;

CREATE TABLE public.native_tenant_mfa_policy (
    tenant_id uuid PRIMARY KEY,
    required boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON public.native_tenant_mfa_policy FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.native_tenant_mfa_policy TO engine_api_runtime;

-- Apply the Required tenant check inside Dovecot's existing password-verifier
-- function so neither IMAPS nor submission can accept primary credentials.
CREATE OR REPLACE FUNCTION public.dovecot_client_auth(candidate_address text, raw text)
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
    tenant_requires boolean;
BEGIN
    IF raw IS NULL OR length(raw) = 0 OR octet_length(raw) > 72 THEN
        RETURN;
    END IF;

    SELECT m.id, m.address, m.password_hash,
           coalesce(p.client_access_enabled, true),
           coalesce(p.dedicated_password_required, false),
           coalesce(t.required, false)
      INTO target_id, target_address, primary_hash, permitted, dedicated, tenant_requires
      FROM public.mailbox m
      JOIN public.domain d ON d.id = m.domain_id
      LEFT JOIN public.mail_client_policy p ON p.mailbox_id = m.id
      LEFT JOIN public.native_tenant_mfa_policy t ON t.tenant_id = d.tenant_id
     WHERE m.address = lower(candidate_address)
       AND m.active AND m.login_enabled AND d.active;

    IF target_id IS NULL OR NOT permitted THEN
        RETURN;
    END IF;

    -- A Required tenant may use only dedicated credentials. Before a
    -- mailbox enrolls, even an otherwise valid primary password is rejected.
    -- Missing tenant mapping does NOT authorize Require activation (the
    -- control plane must verify every domain is mapped first).
    IF tenant_requires AND NOT dedicated THEN
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


