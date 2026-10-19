-- Stage 7C: crash-safe per-mailbox pending IMAP session disconnects.
-- No legacy credential/authentication change. Runtime role is the ONLY writer.
CREATE TABLE public.mail_client_kick_pending (
    mailbox_id bigint PRIMARY KEY REFERENCES public.mailbox(id) ON DELETE CASCADE,
    generation uuid NOT NULL DEFAULT gen_random_uuid(),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_failure_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX mail_client_kick_due_idx
    ON public.mail_client_kick_pending(next_attempt_at, mailbox_id);
REVOKE ALL ON public.mail_client_kick_pending
    FROM PUBLIC, engine_ro_postfix, engine_ro_dovecot;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.mail_client_kick_pending
    TO engine_api_runtime;
