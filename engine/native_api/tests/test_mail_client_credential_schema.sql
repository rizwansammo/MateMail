-- Stage 7A: run ONLY against isolated, disposable Native Engine test DB.
BEGIN;
INSERT INTO public.domain(name) VALUES ('phase7a-credential-test.example.test');
INSERT INTO public.mailbox(domain_id, address, local_part, password_hash)
SELECT id, 'alice@phase7a-credential-test.example.test', 'alice',
       '{BLF-CRYPT}$2b$10$' || repeat('a', 53)
FROM public.domain WHERE name='phase7a-credential-test.example.test';

-- A labeled, scheme-prefixed credential is structurally accepted.
INSERT INTO public.mail_client_password(mailbox_id,label,password_hash)
SELECT id, 'Test Desktop', '{BLF-CRYPT}$2b$10$' || repeat('b', 53)
FROM public.mailbox WHERE address='alice@phase7a-credential-test.example.test';

-- Plaintext must fail at the database level even if an API makes a mistake.
DO $test$
BEGIN
  BEGIN
    INSERT INTO public.mail_client_password(mailbox_id,label,password_hash)
    SELECT id, 'Unsafe', 'cleartext-secret'
    FROM public.mailbox WHERE address='alice@phase7a-credential-test.example.test';
    RAISE EXCEPTION 'plaintext was accepted by credential constraint';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;
END
$test$;

DO $test$
BEGIN
 IF has_table_privilege('engine_ro_postfix','public.mail_client_password','SELECT') OR
    has_table_privilege('engine_ro_dovecot','public.mail_client_password','SELECT') THEN
    RAISE EXCEPTION 'mail readers can directly read client credential table';
 END IF;
END
$test$;
ROLLBACK;
