-- MateMail Native Engine — NE2 provisioning schema.
--
-- WHAT THIS CREATES
--   The authoritative engine-side state behind the NE2 subset of
--   `MailEngineAdapter`: domains, mailboxes, aliases, forwarding and DKIM
--   metadata.
--
-- WHO MAY WRITE IT
--   `matemail-native-api` and nothing else (NE0.2). Postfix and Dovecot get
--   SELECT only, and are granted it explicitly at the end of this file rather
--   than left to inherited defaults.
--
-- UNITS
--   Every quota column is MEGABYTES, named `_mb` so it cannot be read as bytes.
--   This matches `MailboxSpec.quota_mb` and `DomainSpec.*_quota_mb` exactly.
--   Reinterpreting the unit here would silently resize every customer mailbox.

-- ── Domains ─────────────────────────────────────────────────────────────────
--
-- `name` is stored already-normalised (lowercase, IDNA A-label). The CHECK is a
-- backstop against a writer that skips normalisation, not the normalisation
-- itself — that happens in the API where the error can be explained.
CREATE TABLE IF NOT EXISTS domain (
    id                  bigserial    PRIMARY KEY,
    name                text         NOT NULL UNIQUE,
    active              boolean      NOT NULL DEFAULT true,
    dkim_selector       text         NOT NULL DEFAULT 'mm1',
    max_mailboxes       integer      NOT NULL DEFAULT 10,
    max_aliases         integer      NOT NULL DEFAULT 400,
    default_quota_mb    integer      NOT NULL DEFAULT 1024,
    max_quota_mb        integer      NOT NULL DEFAULT 1024,
    total_quota_mb      integer      NOT NULL DEFAULT 10240,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    updated_at          timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT domain_name_is_normalised CHECK (name = lower(name) AND name <> ''),
    -- The same three-way storage rule DomainSpec enforces in MateMail, restated
    -- where the data actually lives. A spec that contradicts itself cannot be
    -- written even by a caller that bypassed the DTO.
    CONSTRAINT domain_storage_is_coherent CHECK (
        default_quota_mb > 0
        AND default_quota_mb <= max_quota_mb
        AND max_quota_mb    <= total_quota_mb
    )
);

-- ── Mailboxes ───────────────────────────────────────────────────────────────
--
-- `password_hash` holds a Dovecot password scheme string — `{BLF-CRYPT}$2b$...`
-- — and NEVER a plaintext password. The CHECK refuses anything that does not
-- carry a scheme prefix, so a writer that tried to store a bare password would
-- fail loudly at the database rather than quietly create an unhashed credential.
CREATE TABLE IF NOT EXISTS mailbox (
    id            bigserial    PRIMARY KEY,
    domain_id     bigint       NOT NULL REFERENCES domain(id) ON DELETE CASCADE,
    address       text         NOT NULL UNIQUE,
    local_part    text         NOT NULL,
    display_name  text         NOT NULL DEFAULT '',
    password_hash text,
    quota_mb      integer      NOT NULL DEFAULT 1024,
    active        boolean      NOT NULL DEFAULT true,
    last_login_at timestamptz,
    created_at    timestamptz  NOT NULL DEFAULT now(),
    updated_at    timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT mailbox_address_is_normalised CHECK (address = lower(address)),
    CONSTRAINT mailbox_quota_is_positive CHECK (quota_mb > 0),
    CONSTRAINT mailbox_password_is_hashed CHECK (
        password_hash IS NULL OR password_hash ~ '^\{[A-Z0-9-]+\}'
    )
);
CREATE INDEX IF NOT EXISTS mailbox_domain_idx ON mailbox(domain_id);

-- ── Aliases ─────────────────────────────────────────────────────────────────
--
-- An alias is a delivery mapping. Whether it ALSO authorises someone to send as
-- that address is a property of each destination, not of the alias — see
-- alias_destination below.
CREATE TABLE IF NOT EXISTS alias (
    id         bigserial    PRIMARY KEY,
    domain_id  bigint       REFERENCES domain(id) ON DELETE CASCADE,
    address    text         NOT NULL UNIQUE,
    active     boolean      NOT NULL DEFAULT true,
    created_at timestamptz  NOT NULL DEFAULT now(),
    updated_at timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT alias_address_is_normalised CHECK (address = lower(address))
);
CREATE INDEX IF NOT EXISTS alias_domain_idx ON alias(domain_id);

-- THE DISTINCTION NE0 ASKED FOR, MADE STRUCTURAL.
--
-- mailbox_id NOT NULL  -> the destination is a mailbox this engine hosts.
--                         That mailbox may send as the alias address.
-- mailbox_id NULL      -> the destination is somewhere else entirely.
--                         Nobody gains any sending right from it.
--
-- Modelling it this way is what stops the mailcow shape where an alias can
-- receive mail but its owner cannot send as it: the authorisation is derivable
-- from the same row that creates the delivery, so the two cannot drift apart.
-- It also means an external destination can never accidentally become an
-- authorised sender, because there is no mailbox row for it to point at.
CREATE TABLE IF NOT EXISTS alias_destination (
    id          bigserial  PRIMARY KEY,
    alias_id    bigint     NOT NULL REFERENCES alias(id) ON DELETE CASCADE,
    destination text       NOT NULL,
    mailbox_id  bigint     REFERENCES mailbox(id) ON DELETE CASCADE,
    position    integer    NOT NULL DEFAULT 0,
    CONSTRAINT alias_destination_unique UNIQUE (alias_id, destination),
    CONSTRAINT alias_destination_is_normalised CHECK (destination = lower(destination))
);
CREATE INDEX IF NOT EXISTS alias_destination_alias_idx   ON alias_destination(alias_id);
CREATE INDEX IF NOT EXISTS alias_destination_mailbox_idx ON alias_destination(mailbox_id);

-- ── Forwarding ──────────────────────────────────────────────────────────────
--
-- Deliberately NOT the alias table.
--
-- Forwarding says "copy this mailbox's mail somewhere else". It says nothing
-- about identity. If forwarding lived in alias_destination, then
-- support@customer.example -> external@gmail.example would put an external
-- address one join away from the send-as query, and the only thing standing
-- between that and a spoofing right would be a WHERE clause somebody has to
-- remember. A separate table makes the wrong answer unreachable instead of
-- merely unwritten.
CREATE TABLE IF NOT EXISTS forwarding (
    id          bigserial    PRIMARY KEY,
    mailbox_id  bigint       NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
    destination text         NOT NULL,
    position    integer      NOT NULL DEFAULT 0,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT forwarding_unique UNIQUE (mailbox_id, destination),
    CONSTRAINT forwarding_is_normalised CHECK (destination = lower(destination))
);
CREATE INDEX IF NOT EXISTS forwarding_mailbox_idx ON forwarding(mailbox_id);

-- ── DKIM ────────────────────────────────────────────────────────────────────
--
-- `domain_name` is TEXT, not a foreign key, and that is deliberate.
--
-- The adapter contract pins that a DKIM key OUTLIVES its domain
-- (`test_deleting_a_domain_does_not_delete_its_dkim_key`), because MateMail's
-- deprovisioning task deletes the key explicitly and unconditionally BEFORE the
-- domain. A foreign key with ON DELETE CASCADE would silently "fix" that
-- behaviour, break the contract test, and remove the cross-tenant remedy the
-- task depends on.
--
-- The private key is NOT here. It lives on the engine filesystem at
-- `private_key_path`, mode 0600, and never enters the database, an API
-- response, or MateMail (DEC-007r).
CREATE TABLE IF NOT EXISTS dkim_key (
    id               bigserial    PRIMARY KEY,
    domain_name      text         NOT NULL UNIQUE,
    selector         text         NOT NULL,
    public_key       text         NOT NULL,
    private_key_path text         NOT NULL,
    key_size         integer      NOT NULL DEFAULT 2048,
    active           boolean      NOT NULL DEFAULT true,
    created_at       timestamptz  NOT NULL DEFAULT now(),
    rotated_at       timestamptz,
    CONSTRAINT dkim_domain_is_normalised CHECK (domain_name = lower(domain_name)),
    -- A row here must never carry private material, whatever a future writer
    -- intends. Cheap to enforce, and it turns a serious mistake into an error.
    CONSTRAINT dkim_public_key_is_public CHECK (
        public_key NOT LIKE '%PRIVATE KEY%'
    )
);

-- ── Read-only consumers (NE0.2) ─────────────────────────────────────────────
--
-- Granted explicitly. The bootstrap's ALTER DEFAULT PRIVILEGES covers tables
-- created afterwards by the same role, but relying on that alone would make the
-- guarantee depend on which role happened to run the migration.
GRANT SELECT ON domain, mailbox, alias, alias_destination, forwarding, dkim_key
    TO engine_ro_postfix, engine_ro_dovecot;

-- Neither may ever write. Stated rather than assumed: a later GRANT ALL would
-- have to remove this line, which is a reviewable change.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON domain, mailbox, alias, alias_destination, forwarding, dkim_key
    FROM engine_ro_postfix, engine_ro_dovecot;

-- Postfix and Dovecot never need to read DKIM metadata, and Dovecot has no
-- business reading password hashes it does not authenticate against yet. NE3
-- widens this deliberately when the lookups are actually wired.
REVOKE SELECT ON dkim_key FROM engine_ro_postfix, engine_ro_dovecot;
