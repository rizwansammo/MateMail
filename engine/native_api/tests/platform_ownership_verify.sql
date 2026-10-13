\set ON_ERROR_STOP on
CREATE OR REPLACE FUNCTION expected_denial(statement text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE statement;
    RAISE EXCEPTION 'Security regression: statement was permitted: %', statement;
EXCEPTION WHEN insufficient_privilege THEN
    RETURN;
END;
$$;
SELECT expected_denial('INSERT INTO domain(name) VALUES (''system.matemail.pro'')');
SELECT expected_denial('INSERT INTO domain(name) VALUES (''mx.matemail.pro'')');
SELECT expected_denial('INSERT INTO domain(name) VALUES (''portal.matemail.pro'')');
SELECT expected_denial('UPDATE domain SET active=false WHERE name=''mail.matemail.pro''');
SELECT expected_denial('DELETE FROM domain WHERE name=''mail.matemail.pro''');
SELECT expected_denial('UPDATE mailbox SET active=false WHERE address=''noreply@mail.matemail.pro''');
SELECT expected_denial('UPDATE mailbox SET quota_mb=200 WHERE address=''noreply@mail.matemail.pro''');
SELECT expected_denial('UPDATE mailbox SET storage_id=''stolen-storage'' WHERE address=''noreply@mail.matemail.pro''');
SELECT expected_denial('UPDATE mailbox SET login_enabled=false WHERE address=''noreply@mail.matemail.pro''');
SELECT expected_denial('UPDATE mailbox SET password_hash=''{BLF-CRYPT}$2b$12$replacement'' WHERE address=''noreply@mail.matemail.pro''');
SELECT expected_denial('DELETE FROM mailbox WHERE address=''noreply@mail.matemail.pro''');
SELECT expected_denial('INSERT INTO mailbox(domain_id,address,local_part,login_enabled) SELECT id,''new@mail.matemail.pro'',''new'',false FROM domain WHERE name=''mail.matemail.pro''');
-- Legitimate login auditing and unrelated customer domain updates must still work.
UPDATE mailbox SET last_login_at = NOW() WHERE address='noreply@mail.matemail.pro';
UPDATE domain SET active=false WHERE name='customer.example';
DO $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM mailbox WHERE address='noreply@mail.matemail.pro'
                  AND last_login_at IS NOT NULL AND active) THEN
        RAISE EXCEPTION 'Legacy system mailbox state or login audit was changed';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM domain WHERE name='customer.example' AND NOT active) THEN
        RAISE EXCEPTION 'Customer domain lifecycle has regressed';
    END IF;
END $$;
SELECT 'PLATFORM_OWNERSHIP_SQL_REGRESSION_PASS';
