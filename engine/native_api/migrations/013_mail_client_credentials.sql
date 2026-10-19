-- Stage 7A: dedicated SMTP/IMAP credential storage; NO authentication cutover.
--
-- The engine remains the ONLY writer of mailbox credentials. Native readers
-- must not see these tables directly. The existing dovecot_auth view is not
-- changed until a tested, fail-closed multi-credential authentication path is
-- deployed. Absence of a policy row preserves the legacy client behavior.
CREATE TABLE public.mail_client_policy (
    mailbox_id bigint PRIMARY KEY REFERENCES public.mailbox(id) ON DELETE CASCADE,
    client_access_enabled boolean NOT NULL DEFAULT true,
    dedicated_password_required boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.mail_client_password (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mailbox_id bigint NOT NULL REFERENCES public.mailbox(id) ON DELETE CASCADE,
    label varchar(80) NOT NULL,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    CONSTRAINT mail_client_password_label_check CHECK (length(btrim(label)) BETWEEN 1 AND 80),
    CONSTRAINT mail_client_password_bcrypt_check CHECK (
        password_hash ~ '^\{BLF-CRYPT\}\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$'
    )
);
CREATE INDEX mail_client_password_active_mailbox_idx
    ON public.mail_client_password(mailbox_id)
    WHERE revoked_at IS NULL;

-- Explicit least privilege even if a former default-privilege grant returns.
REVOKE ALL ON public.mail_client_policy, public.mail_client_password
    FROM PUBLIC, engine_ro_postfix, engine_ro_dovecot;
