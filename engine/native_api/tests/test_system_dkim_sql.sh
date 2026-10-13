#!/usr/bin/env bash
# CI only: disposable PostgreSQL container and synthetic public key.
# Never point this at a real database or run on MateServer.
set -euo pipefail
name="matemail-dkim-ci-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$name" -e POSTGRES_PASSWORD=ci-only-not-a-real-secret \
    -e POSTGRES_DB=matemail_engine postgres:16-alpine >/dev/null
for _ in $(seq 1 45); do
    if docker exec "$name" pg_isready -U postgres -d matemail_engine >/dev/null 2>&1; then break; fi
    sleep 1
done
docker exec "$name" pg_isready -U postgres -d matemail_engine >/dev/null
for migration in engine/native_api/migrations/[0-9][0-9][0-9]_*.sql; do
    docker exec -i "$name" psql -X -v ON_ERROR_STOP=1 -U postgres -d matemail_engine < "$migration" >/dev/null
done
docker exec -i "$name" psql -X -v ON_ERROR_STOP=1 -U postgres -d matemail_engine <<'SQL'
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
