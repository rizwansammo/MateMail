-- MateMail Native Engine — NE4 operational state.
--
-- WHAT THIS ADDS
--   1. An immutable storage identity for every mailbox, so deleting and
--      recreating an address cannot expose the previous owner's mail.
--   2. Per-mailbox submission rate limits the mail path actually enforces.
--   3. A record of retained storage belonging to deleted mailboxes, so that
--      data has an owner instead of being an unexplained directory.

-- ── Immutable mailbox storage identity ──────────────────────────────────────
--
-- THE PROBLEM THIS FIXES, found during NE3 runtime validation.
--
--   Maildir lived at /var/vmail/<domain>/<local part>. Deleting a mailbox
--   removed its rows and left the mail on disk. Recreating the same address
--   therefore pointed a NEW owner at the OLD owner's mail — a silent
--   cross-customer disclosure that needs no attacker, only staff recreating a
--   mailbox somebody asked to have deleted.
--
-- THE FIX
--   Storage is addressed by an opaque identity that is minted once per mailbox
--   ROW and never reused. A recreated address is a new row, so it gets a new
--   identity and a different directory. The old directory becomes unreachable
--   by construction rather than by remembering to delete it.
--
-- WHY EXISTING ROWS KEEP THEIR CURRENT PATH
--   Backfilling a fresh identity for rows that already exist would rename every
--   live mailbox's storage while its mail sits at the old path — the migration
--   would orphan exactly the data it is meant to protect, and SQL cannot move
--   the files. Existing rows are therefore backfilled with their CURRENT
--   `local_part`, which is precisely the directory they already use: zero data
--   movement, and nothing to migrate on disk.
--
--   That does not weaken the fix. The vulnerability is delete-then-recreate,
--   and a recreated mailbox is always a NEW row, which always gets a UUID.
--   Grandfathered rows keep a legacy-shaped identity only until someone deletes
--   them, and their replacement is safe.
--
-- WHY TEXT AND NOT uuid
--   The column has to hold both a UUID and a grandfathered local part. Typing
--   it `uuid` would force the backfill to invent identities, which is the
--   behaviour rejected above.
ALTER TABLE mailbox
    ADD COLUMN IF NOT EXISTS storage_id text;

-- Existing rows: the directory they are already using.
UPDATE mailbox SET storage_id = local_part WHERE storage_id IS NULL;

-- New rows: an opaque identity. gen_random_uuid() is built into PostgreSQL 13+,
-- so this needs no extension.
ALTER TABLE mailbox
    ALTER COLUMN storage_id SET DEFAULT gen_random_uuid()::text;
ALTER TABLE mailbox
    ALTER COLUMN storage_id SET NOT NULL;

-- A path component, so it must never contain a separator or traversal. The
-- CHECK is what makes "this value is safe to concatenate into a filesystem
-- path" a property of the data rather than a promise made by the code that
-- writes it.
ALTER TABLE mailbox DROP CONSTRAINT IF EXISTS mailbox_storage_id_is_a_safe_path;
ALTER TABLE mailbox ADD CONSTRAINT mailbox_storage_id_is_a_safe_path
    CHECK (storage_id ~ '^[A-Za-z0-9._-]+$' AND storage_id NOT IN ('.', '..'));

-- Two live mailboxes must never share a directory.
CREATE UNIQUE INDEX IF NOT EXISTS mailbox_storage_id_unique ON mailbox(storage_id);

-- ── Retained storage of deleted mailboxes ───────────────────────────────────
--
-- Deleting a mailbox deliberately does NOT delete its mail (NE4 decision): a
-- provisioning call must not be able to destroy customer data as a side effect,
-- and restore is P6's. But retained data with no record is just an orphan
-- directory nobody dares touch, so the engine writes down what it kept.
--
-- P6 owns the retention policy and the eventual deletion. This table is what
-- makes that possible without guessing which directories are safe to remove.
CREATE TABLE IF NOT EXISTS retired_mailbox_storage (
    id           bigserial   PRIMARY KEY,
    address      text        NOT NULL,
    domain_name  text        NOT NULL,
    storage_id   text        NOT NULL,
    quota_mb     integer,
    retired_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT retired_storage_unique UNIQUE (storage_id)
);
CREATE INDEX IF NOT EXISTS retired_mailbox_storage_address_idx
    ON retired_mailbox_storage(address);

-- ── Per-mailbox submission rate limits ──────────────────────────────────────
--
-- The engine's OWN limit, applied at submission. Deliberately separate from
-- MateMail's application limiter (P5): that one is consulted through the policy
-- bridge and reasons in product terms, while this one holds even for a client
-- that reaches the engine without passing through MateMail at all.
--
-- One row per mailbox, deleted to clear. `messages = 0` means an explicit "no
-- limit" per the RateLimit DTO, which is NOT the same as having no row — no row
-- means nothing was ever configured.
CREATE TABLE IF NOT EXISTS mailbox_rate_limit (
    mailbox_id  bigint      PRIMARY KEY REFERENCES mailbox(id) ON DELETE CASCADE,
    messages    integer     NOT NULL,
    -- `window` on its own is a reserved word in PostgreSQL (window functions),
    -- so the column carries the suffix rather than needing quotes at every use.
    window_name text        NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rate_limit_messages_not_negative CHECK (messages >= 0),
    -- The same four windows the port accepts. Stated here so a writer that
    -- bypassed the DTO cannot store a window the enforcement cannot interpret.
    CONSTRAINT rate_limit_window_is_known
        CHECK (window_name IN ('second', 'minute', 'hour', 'day'))
);

-- ── Views ───────────────────────────────────────────────────────────────────

-- Storage now comes from the immutable identity, not the address. This is the
-- single change that makes a recreated mailbox land somewhere new.
CREATE OR REPLACE VIEW dovecot_userdb AS
    SELECT m.address,
           '/var/vmail/' || d.name || '/' || m.storage_id AS mail_home,
           m.quota_mb::text || 'M' AS quota_storage_size,
           m.quota_mb,
           m.storage_id
    FROM mailbox m
    JOIN domain d ON d.id = m.domain_id
    WHERE m.active AND d.active;

-- What the submission policy service reads to decide whether a mailbox has
-- exceeded its limit. The window is published in SECONDS so the enforcement
-- never has to parse a word, and an unconfigured mailbox simply does not appear.
CREATE OR REPLACE VIEW postfix_rate_limit AS
    SELECT m.address,
           rl.messages,
           CASE rl.window_name
               WHEN 'second' THEN 1
               WHEN 'minute' THEN 60
               WHEN 'hour'   THEN 3600
               WHEN 'day'    THEN 86400
           END AS window_seconds,
           rl.window_name
    FROM mailbox_rate_limit rl
    JOIN mailbox m ON m.id = rl.mailbox_id
    JOIN domain d ON d.id = m.domain_id
    WHERE m.active AND d.active;

-- ── Grants ──────────────────────────────────────────────────────────────────
--
-- Migration 003 retired the default-privilege rule, so a new relation is
-- readable by nobody until it is named here. Both views are therefore granted
-- explicitly, and the new TABLES are granted to no one: the readers see views.
REVOKE ALL ON dovecot_userdb, postfix_rate_limit
    FROM engine_ro_postfix, engine_ro_dovecot;
REVOKE ALL ON mailbox_rate_limit, retired_mailbox_storage
    FROM engine_ro_postfix, engine_ro_dovecot;

GRANT SELECT ON dovecot_userdb TO engine_ro_dovecot;
GRANT SELECT ON postfix_rate_limit TO engine_ro_postfix;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON dovecot_userdb, postfix_rate_limit
    FROM engine_ro_postfix, engine_ro_dovecot;
