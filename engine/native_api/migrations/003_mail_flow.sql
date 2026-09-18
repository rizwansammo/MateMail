-- MateMail Native Engine — NE3 mail-flow read contract.
--
-- WHAT THIS CREATES
--   The views Postfix and Dovecot look mail up through, and the LOGIN rights
--   that let them connect at all. No new state: NE2's tables remain the only
--   place anything is stored, and `matemail-native-api` remains the only writer.
--
-- WHY VIEWS AND NOT THE BASE TABLES
--   NE2 granted the two reader roles SELECT on the tables themselves. That works
--   until the schema moves, and then a column rename silently breaks a Postfix
--   map at delivery time rather than at deploy time.
--
--   A view is a contract. Postfix and Dovecot depend on these six shapes; the
--   tables underneath can be reorganised as long as the views still answer. It
--   also lets each role see only what it needs — `password_hash` is exposed to
--   Dovecot and to nothing else.
--
-- THE ACTIVE RULES LIVE HERE, ONCE
--   "A mailbox is usable when the mailbox is active AND its domain is active" is
--   a rule that would otherwise be copy-pasted into every map file and every
--   query, where one copy eventually disagrees with the others. Encoding it in
--   the views means a suspended domain disables submission, delivery and IMAP by
--   the same definition.

-- ── Postfix: is this domain ours? ───────────────────────────────────────────
CREATE OR REPLACE VIEW postfix_virtual_domain AS
    SELECT d.name
    FROM domain d
    WHERE d.active;

-- ── Postfix: does this mailbox exist and may it receive? ────────────────────
CREATE OR REPLACE VIEW postfix_virtual_mailbox AS
    SELECT m.address,
           d.name AS domain
    FROM mailbox m
    JOIN domain d ON d.id = m.domain_id
    WHERE m.active AND d.active;

-- ── Postfix: where does mail for this address actually go? ──────────────────
--
-- DELIVERY, and only delivery. Two sources, deliberately unioned here rather
-- than merged in the schema:
--
--   alias        someone published an address that routes to one or more places
--   forwarding   a mailbox owner asked for copies to go elsewhere
--
-- Both answer the same question — "deliver mail for X to Y" — so Postfix wants
-- them in one map. They stay SEPARATE TABLES because only the first can confer
-- the right to SEND as an address, and that distinction is enforced by the
-- send-as view below never looking at `forwarding` at all.
--
-- Forwarding replaces delivery unless the mailbox itself is in the set. That is
-- MateMail's decision, not the engine's: `ForwardingSpec.destinations` is
-- documented as the final resolved set, including the mailbox when a copy
-- should be kept. The engine stores exactly what it was given.
CREATE OR REPLACE VIEW postfix_virtual_alias AS
    SELECT a.address,
           ad.destination,
           ad.position
    FROM alias a
    JOIN alias_destination ad ON ad.alias_id = a.id
    JOIN domain d ON d.id = a.domain_id
    WHERE a.active AND d.active
    UNION ALL
    SELECT m.address,
           f.destination,
           f.position
    FROM forwarding f
    JOIN mailbox m ON m.id = f.mailbox_id
    JOIN domain d ON d.id = m.domain_id
    WHERE m.active AND d.active;

-- ── Postfix: who is allowed to SEND as this address? ────────────────────────
--
-- THE security-critical view. `smtpd_sender_login_maps` reads it, and
-- `reject_authenticated_sender_login_mismatch` refuses any MAIL FROM whose
-- authenticated identity is not among the owners returned here.
--
-- Exactly two things grant ownership:
--
--   1. a mailbox owns its own address
--   2. an alias whose destination IS a mailbox this engine hosts
--      (alias_destination.mailbox_id IS NOT NULL) is owned by that mailbox
--
-- It does NOT read `forwarding`. Not "filters it out" — does not reference the
-- table, so no future WHERE-clause edit can accidentally admit it. An external
-- alias destination has a NULL mailbox_id and is excluded by the INNER JOIN for
-- the same structural reason.
--
-- This is also what fixes the mailcow limitation the product hit: an internal
-- alias genuinely authorises its destination mailbox to send as it, rather than
-- being deliverable but unusable as an identity.
CREATE OR REPLACE VIEW postfix_sender_login AS
    SELECT m.address,
           m.address AS owner
    FROM mailbox m
    JOIN domain d ON d.id = m.domain_id
    WHERE m.active AND d.active
    UNION
    SELECT a.address,
           owner_mb.address AS owner
    FROM alias a
    JOIN alias_destination ad ON ad.alias_id = a.id
    JOIN mailbox owner_mb ON owner_mb.id = ad.mailbox_id
    JOIN domain alias_domain ON alias_domain.id = a.domain_id
    JOIN domain owner_domain ON owner_domain.id = owner_mb.domain_id
    WHERE a.active
      AND alias_domain.active
      AND owner_mb.active
      AND owner_domain.active;

-- ── Dovecot: authentication ─────────────────────────────────────────────────
--
-- The ONLY view carrying `password_hash`, and it is granted to
-- engine_ro_dovecot alone. The value is a Dovecot scheme string
-- ({BLF-CRYPT}$2b$...); no plaintext can be here, because the mailbox table's
-- CHECK constraint refuses anything without a scheme prefix.
--
-- A mailbox with a NULL hash is excluded rather than returned: a row with no
-- credential must not be an account that authenticates, and leaving it out
-- means Dovecot sees "unknown user" instead of "user with no password".
CREATE OR REPLACE VIEW dovecot_auth AS
    SELECT m.address,
           m.password_hash
    FROM mailbox m
    JOIN domain d ON d.id = m.domain_id
    WHERE m.active AND d.active AND m.password_hash IS NOT NULL;

-- ── Dovecot: where the mail lives, and how much may be stored ───────────────
--
-- COLUMN NAMES ARE DOVECOT 2.4 SETTING NAMES, NOT THE 2.3 userdb FIELD NAMES.
-- 2.4 turned userdb fields into settings: `home`, `uid` and `gid` no longer
-- exist and a query returning them fails the lookup. Verified against the
-- pinned image — `doveconf -a` lists mail_home, mail_uid, mail_gid and
-- quota_storage_size, and none of home/uid/gid.
--
-- WHAT THIS VIEW DELIBERATELY DOES NOT RETURN
--   uid and gid. They are not per-user data — every mailbox is delivered as
--   5000:5000 — so they are fixed in dovecot.conf, where first_valid_uid and
--   last_valid_uid pin them shut as well. Serving them from here would mean a
--   database compromise could choose the uid that writes to disk.
--
-- `mail_home` IS served from here, because the layout is derived from stored
-- columns (d.name, m.local_part) rather than re-parsed out of the login name.
--
-- QUOTA UNITS. `quota_mb` is megabytes — the unit the adapter contract uses
-- everywhere — and Dovecot is handed an explicit "<n>M" string. It is not
-- handed a bare number: Dovecot reads that as BYTES, turning a 1024 MB mailbox
-- into a 1 KB one. The suffix is the whole safety property, so the view
-- produces it rather than trusting a format string somewhere else to add it.
CREATE OR REPLACE VIEW dovecot_userdb AS
    SELECT m.address,
           '/var/vmail/' || d.name || '/' || m.local_part AS mail_home,
           m.quota_mb::text || 'M' AS quota_storage_size,
           m.quota_mb
    FROM mailbox m
    JOIN domain d ON d.id = m.domain_id
    WHERE m.active AND d.active;

-- ── Least privilege ─────────────────────────────────────────────────────────
--
-- LOGIN is granted because NE3 is when these roles actually connect. Passwords
-- are NOT set here — they never enter Git. `matemail-native-api` sets them at
-- startup from its environment, which is the same component that already owns
-- every other write to this database.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engine_ro_postfix') THEN
        CREATE ROLE engine_ro_postfix NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'engine_ro_dovecot') THEN
        CREATE ROLE engine_ro_dovecot NOLOGIN;
    END IF;
END
$$;

ALTER ROLE engine_ro_postfix WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
ALTER ROLE engine_ro_dovecot WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;

-- ── Retire the NE1 default-privilege rule ───────────────────────────────────
--
-- NE1's bootstrap carries:
--
--     ALTER DEFAULT PRIVILEGES IN SCHEMA public
--         GRANT SELECT ON TABLES TO engine_ro_postfix, engine_ro_dovecot;
--
-- It was meant as a safety rail — new tables start read-only — but it does the
-- opposite of least privilege: it grants BOTH readers SELECT on every relation
-- `engine` creates from then on, automatically and silently. "ON TABLES"
-- includes VIEWS, so without this the six views below are readable by both
-- roles no matter what the GRANTs further down say, and `engine_ro_postfix`
-- can read every password hash in `dovecot_auth`.
--
-- This is not hypothetical. The rule already fired once: migration 002 had to
-- append an explicit `REVOKE SELECT ON dkim_key` to claw back access it never
-- granted. Whatever table a future migration adds — a rate-limit counter, a
-- token store — would be exposed the same way, and that REVOKE would have to be
-- remembered every time.
--
-- Default-deny is the correct rail. From here a new relation is readable by
-- nobody until a migration says otherwise.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE SELECT ON TABLES FROM engine_ro_postfix, engine_ro_dovecot;

-- The rule above only governs relations created AFTER it. The six views were
-- created above, under the old rule, so they already carry the automatic grant
-- and it has to be taken back explicitly before the real grants are made.
REVOKE ALL ON postfix_virtual_domain, postfix_virtual_mailbox,
               postfix_virtual_alias, postfix_sender_login,
               dovecot_auth, dovecot_userdb
    FROM engine_ro_postfix, engine_ro_dovecot;

-- Take away the NE2 table-level access. From here the readers see views only,
-- so a schema change cannot reach them and they cannot read a column the view
-- does not publish.
REVOKE ALL ON domain, mailbox, alias, alias_destination, forwarding, dkim_key
    FROM engine_ro_postfix, engine_ro_dovecot;

-- Postfix: routing and sender ownership. It is NOT granted dovecot_auth, so a
-- compromised Postfix cannot read a single password hash.
GRANT SELECT ON postfix_virtual_domain, postfix_virtual_mailbox,
                postfix_virtual_alias, postfix_sender_login
    TO engine_ro_postfix;

-- Dovecot: credentials and mail location. It is NOT granted the Postfix views;
-- it has no business knowing the routing table.
GRANT SELECT ON dovecot_auth, dovecot_userdb
    TO engine_ro_dovecot;

-- Neither may ever write, to a view or through one.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON
    postfix_virtual_domain, postfix_virtual_mailbox, postfix_virtual_alias,
    postfix_sender_login, dovecot_auth, dovecot_userdb
    FROM engine_ro_postfix, engine_ro_dovecot;

-- And neither may create anything of its own to write into.
REVOKE CREATE ON SCHEMA public FROM engine_ro_postfix, engine_ro_dovecot;
