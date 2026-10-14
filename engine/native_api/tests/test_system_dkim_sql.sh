#!/usr/bin/env bash
# CI only: disposable PostgreSQL container and synthetic public key.
# Never point this at a real database or run on MateServer.
set -euo pipefail
name="matemail-dkim-ci-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
echo 'Starting disposable PostgreSQL 16 container'
docker run -d --name "$name" -e POSTGRES_PASSWORD=ci-only-not-a-real-secret \
    -e POSTGRES_DB=matemail_engine postgres:16-alpine >/dev/null
for _ in $(seq 1 45); do
    if docker exec "$name" pg_isready -h 127.0.0.1 -U postgres -d matemail_engine >/dev/null 2>&1; then break; fi
    sleep 1
done
docker exec "$name" pg_isready -h 127.0.0.1 -U postgres -d matemail_engine || { docker logs "$name" | tail -20; exit 1; }
echo 'Disposable database is accepting connections'
for migration in engine/native_api/migrations/[0-9][0-9][0-9]_*.sql; do
    echo "Applying $migration"
    docker exec -i "$name" psql -h 127.0.0.1 -X -v ON_ERROR_STOP=1 -U postgres -d matemail_engine < "$migration" >/dev/null
done
docker exec -i "$name" psql -h 127.0.0.1 -X -v ON_ERROR_STOP=1 -U postgres -d matemail_engine <<'SQL'
-- A platform role may only call the explicit SECURITY DEFINER functions.
SELECT (NOT has_table_privilege('engine_platform_operator', 'public.dkim_key', 'INSERT')) AS no_table_write;
SELECT (NOT has_function_privilege('engine_api_runtime',
    'public.platform_register_system_dkim(text,text)', 'EXECUTE')) AS runtime_denied;
INSERT INTO public.domain(name, active) VALUES ('customer-canary.example', true);
SET SESSION AUTHORIZATION engine_platform_operator;
SELECT public.platform_stage_system_mail();
SELECT public.platform_register_system_dkim(
    repeat('A', 400),
    '/var/lib/rspamd/dkim/system.matemail.pro.mm1.g0123456789abcdef.key'
);
SELECT public.platform_register_system_dkim(
    repeat('A', 400),
    '/var/lib/rspamd/dkim/system.matemail.pro.mm1.g0123456789abcdef.key'
);
RESET SESSION AUTHORIZATION;
DO $$
BEGIN
    IF (SELECT count(*) FROM public.domain
        WHERE name='system.matemail.pro' AND active=false) <> 1 THEN
        RAISE EXCEPTION 'system domain not staged inactive';
    END IF;
    IF (SELECT count(*) FROM public.mailbox m JOIN public.domain d
        ON d.id=m.domain_id WHERE d.name='system.matemail.pro'
        AND m.active=false AND m.login_enabled=false
        AND m.password_hash IS NULL) <> 5 THEN
        RAISE EXCEPTION 'inactive mailbox invariants violated';
    END IF;
    IF (SELECT count(*) FROM public.dkim_key
        WHERE domain_name='system.matemail.pro' AND selector='mm1') <> 1 THEN
        RAISE EXCEPTION 'system DKIM metadata missing';
    END IF;
    IF (SELECT count(*) FROM public.domain
        WHERE name='customer-canary.example' AND active=true) <> 1 THEN
        RAISE EXCEPTION 'customer domain changed';
    END IF;
END $$;
SQL
echo 'Release 3 platform DKIM SQL isolation and inactive staging passed'

echo 'Testing restricted operations canary activation and rollback'
docker exec -i "$name" psql -h 127.0.0.1 -X -v ON_ERROR_STOP=1 -U postgres -d matemail_engine <<'SQL'
SET SESSION AUTHORIZATION engine_platform_operator;
SELECT public.platform_activate_system_canary(
    '{BLF-CRYPT}$2b$10$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
);
RESET SESSION AUTHORIZATION;
DO $$
BEGIN
    IF (SELECT active FROM public.domain WHERE name='system.matemail.pro') IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'canary domain did not activate';
    END IF;
    IF (SELECT count(*) FROM public.postfix_virtual_mailbox WHERE domain='system.matemail.pro') <> 1 THEN
        RAISE EXCEPTION 'exactly one system recipient should be routable';
    END IF;
    IF (SELECT count(*) FROM public.dovecot_auth WHERE address='operations@system.matemail.pro') <> 1 THEN
        RAISE EXCEPTION 'canary should authenticate';
    END IF;
    IF (SELECT count(*) FROM public.mailbox m JOIN public.domain d ON d.id=m.domain_id
        WHERE d.name='system.matemail.pro' AND m.active=true) <> 1 THEN
        RAISE EXCEPTION 'extra platform mailbox activated';
    END IF;
    IF (SELECT count(*) FROM public.domain WHERE name='customer-canary.example' AND active=true) <> 1 THEN
        RAISE EXCEPTION 'customer domain was touched';
    END IF;
END $$;
SET SESSION AUTHORIZATION engine_platform_operator;
SELECT public.platform_deactivate_system_canary();
RESET SESSION AUTHORIZATION;
DO $$
BEGIN
    IF (SELECT active FROM public.domain WHERE name='system.matemail.pro') IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'rollback left domain active';
    END IF;
    IF (SELECT count(*) FROM public.mailbox m JOIN public.domain d ON d.id=m.domain_id
        WHERE d.name='system.matemail.pro' AND (m.active OR m.login_enabled OR m.password_hash IS NOT NULL)) <> 0 THEN
        RAISE EXCEPTION 'rollback left a usable mailbox';
    END IF;
    IF (SELECT count(*) FROM public.dkim_key WHERE domain_name='system.matemail.pro') <> 1 THEN
        RAISE EXCEPTION 'rollback must preserve DKIM';
    END IF;
END $$;
SQL
echo 'System operations canary activation and rollback SQL checks passed'

echo 'Validating four fixed system identities and rollback'
docker exec -i "$name" psql -h 127.0.0.1 -X -v ON_ERROR_STOP=1 -U postgres -d matemail_engine <<'SQL'
SET SESSION AUTHORIZATION engine_platform_operator;
SELECT public.platform_activate_system_canary(
    '{BLF-CRYPT}$2b$10$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
);
SELECT public.platform_activate_remaining_system_roles(
    '{BLF-CRYPT}$2b$10$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    '{BLF-CRYPT}$2b$10$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    '{BLF-CRYPT}$2b$10$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    '{BLF-CRYPT}$2b$10$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
);
RESET SESSION AUTHORIZATION;
DO $$
BEGIN
    IF (SELECT count(*) FROM public.postfix_virtual_mailbox
        WHERE domain='system.matemail.pro')<>5 THEN
        RAISE EXCEPTION 'not all five reserved recipient routes active';
    END IF;
    IF (SELECT count(*) FROM public.dovecot_auth
        WHERE address LIKE '%@system.matemail.pro')<>5 THEN
        RAISE EXCEPTION 'not all five reserved identities authenticatable';
    END IF;
    IF (SELECT count(*) FROM public.postfix_sender_login
        WHERE address LIKE '%@system.matemail.pro')<>5 THEN
        RAISE EXCEPTION 'sender identity view count changed';
    END IF;
    IF (SELECT count(*) FROM public.domain
        WHERE name='customer-canary.example' AND active=true)<>1 THEN
        RAISE EXCEPTION 'customer state changed';
    END IF;
END $$;
SET SESSION AUTHORIZATION engine_platform_operator;
SELECT public.platform_deactivate_remaining_system_roles();
RESET SESSION AUTHORIZATION;
DO $$
BEGIN
    IF (SELECT count(*) FROM public.postfix_virtual_mailbox
        WHERE domain='system.matemail.pro')<>1 THEN
        RAISE EXCEPTION 'remaining role rollback did not preserve only operations';
    END IF;
    IF (SELECT count(*) FROM public.mailbox m JOIN public.domain d ON d.id=m.domain_id
        WHERE d.name='system.matemail.pro' AND m.local_part<>'operations'
        AND (m.active OR m.login_enabled OR m.password_hash IS NOT NULL))<>0 THEN
        RAISE EXCEPTION 'inactive system role credentials remain after rollback';
    END IF;
END $$;
SET SESSION AUTHORIZATION engine_platform_operator;
SELECT public.platform_deactivate_system_canary();
RESET SESSION AUTHORIZATION;
SQL
echo 'Five-role activation and independent rollback SQL verified'
