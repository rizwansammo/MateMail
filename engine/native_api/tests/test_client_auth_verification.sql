BEGIN;
INSERT INTO public.domain(name) VALUES ('phase7a-auth.example.test');
INSERT INTO public.mailbox(domain_id,address,local_part,password_hash)
 SELECT id,'user@phase7a-auth.example.test','user',
        '{BLF-CRYPT}' || public.crypt('legacy-pass',public.gen_salt('bf',10))
 FROM public.domain WHERE name='phase7a-auth.example.test';

SET ROLE engine_ro_dovecot;
SELECT count(*) AS dovecot_reader_can_authenticate
  FROM public.dovecot_client_auth('user@phase7a-auth.example.test','legacy-pass');
RESET ROLE;

DO $block$
BEGIN
 IF (SELECT count(*) FROM public.dovecot_client_auth('user@phase7a-auth.example.test','legacy-pass'))<>1 THEN
  RAISE EXCEPTION 'legacy auth fails';
 END IF;
 IF (SELECT count(*) FROM public.dovecot_client_auth('user@phase7a-auth.example.test','not-legacy'))<>0 THEN
  RAISE EXCEPTION 'wrong legacy password accepted';
 END IF;
END $block$;

INSERT INTO public.mail_client_policy(mailbox_id,client_access_enabled)
 SELECT id,true FROM public.mailbox WHERE address='user@phase7a-auth.example.test';
UPDATE public.mail_client_policy SET client_access_enabled=false;
DO $block$
BEGIN
 IF (SELECT count(*) FROM public.dovecot_client_auth('user@phase7a-auth.example.test','legacy-pass'))<>0 THEN
  RAISE EXCEPTION 'disabled client access bypass';
 END IF;
END $block$;
UPDATE public.mail_client_policy SET client_access_enabled=true,dedicated_password_required=true;
INSERT INTO public.mail_client_password(mailbox_id,label,password_hash)
 SELECT id,'Test Thunderbird',
        '{BLF-CRYPT}' || overlay(public.crypt('device-pass',public.gen_salt('bf',10))
                                PLACING 'b' FROM 3 FOR 1)
 FROM public.mailbox WHERE address='user@phase7a-auth.example.test';
DO $block$
BEGIN
 IF (SELECT count(*) FROM public.dovecot_client_auth('user@phase7a-auth.example.test','legacy-pass'))<>0 THEN
  RAISE EXCEPTION '2FA allowed legacy password';
 END IF;
 IF (SELECT count(*) FROM public.dovecot_client_auth('user@phase7a-auth.example.test','device-pass'))<>1 THEN
  RAISE EXCEPTION '2FA denied valid dedicated password';
 END IF;
 IF (SELECT count(*) FROM public.dovecot_client_auth('user@phase7a-auth.example.test','invalid-device'))<>0 THEN
  RAISE EXCEPTION '2FA accepted invalid password';
 END IF;
END $block$;
UPDATE public.mail_client_password SET revoked_at=now();
DO $block$
BEGIN
 IF (SELECT count(*) FROM public.dovecot_client_auth('user@phase7a-auth.example.test','device-pass'))<>0 THEN
  RAISE EXCEPTION 'revoked credential still works';
 END IF;
 IF has_table_privilege('engine_ro_dovecot','public.mail_client_password','SELECT') OR
    has_function_privilege('engine_ro_postfix','public.dovecot_client_auth(text,text)','EXECUTE') OR
    NOT has_function_privilege('engine_ro_dovecot','public.dovecot_client_auth(text,text)','EXECUTE') THEN
  RAISE EXCEPTION 'Native credential permissions are incorrect';
 END IF;
END $block$;
ROLLBACK;
