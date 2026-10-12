\set ON_ERROR_STOP on
-- Only after migrations 001 through 006, in a disposable test database.
-- An existing legacy platform domain and mailbox must survive migration 007.
INSERT INTO domain(name) VALUES ('mail.matemail.pro');
INSERT INTO mailbox(domain_id,address,local_part,display_name,login_enabled,password_hash)
SELECT id,'noreply@mail.matemail.pro','noreply','Test legacy sender',true,'{BLF-CRYPT}$2b$12$test-only'
FROM domain WHERE name='mail.matemail.pro';
INSERT INTO domain(name) VALUES ('customer.example');
INSERT INTO mailbox(domain_id,address,local_part,display_name,login_enabled)
SELECT id,'owner@customer.example','owner','Test customer',false
FROM domain WHERE name='customer.example';
