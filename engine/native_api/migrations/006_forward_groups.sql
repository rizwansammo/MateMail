-- MateMail Native Engine — Forward Groups (FG).
--
-- Forward Groups are distribution addresses, not mailboxes. They have no
-- password, no Dovecot user, no storage and no sender-login ownership.
--
-- Delivery state is separate from Alias and Forwarding state even though
-- Postfix consumes all three through one virtual_alias_maps view.

CREATE TABLE IF NOT EXISTS forward_group (
    id            bigserial    PRIMARY KEY,
    domain_id     bigint       NOT NULL REFERENCES domain(id) ON DELETE CASCADE,
    address       text         NOT NULL UNIQUE,
    active        boolean      NOT NULL DEFAULT true,
    sender_policy text         NOT NULL DEFAULT 'anyone',
    created_at    timestamptz  NOT NULL DEFAULT now(),
    updated_at    timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT forward_group_address_is_normalised
        CHECK (address = lower(address)),
    CONSTRAINT forward_group_sender_policy_valid
        CHECK (sender_policy IN ('anyone', 'organization', 'members', 'selected'))
);
CREATE INDEX IF NOT EXISTS forward_group_domain_idx ON forward_group(domain_id);

CREATE TABLE IF NOT EXISTS forward_group_destination (
    id            bigserial  PRIMARY KEY,
    group_id      bigint     NOT NULL REFERENCES forward_group(id) ON DELETE CASCADE,
    destination   text       NOT NULL,
    position      integer    NOT NULL DEFAULT 0,
    CONSTRAINT forward_group_destination_unique UNIQUE (group_id, destination),
    CONSTRAINT forward_group_destination_is_normalised
        CHECK (destination = lower(destination))
);
CREATE INDEX IF NOT EXISTS forward_group_destination_group_idx
    ON forward_group_destination(group_id);

CREATE TABLE IF NOT EXISTS forward_group_sender (
    id          bigserial  PRIMARY KEY,
    group_id    bigint     NOT NULL REFERENCES forward_group(id) ON DELETE CASCADE,
    sender      text       NOT NULL,
    position    integer    NOT NULL DEFAULT 0,
    CONSTRAINT forward_group_sender_unique UNIQUE (group_id, sender),
    CONSTRAINT forward_group_sender_is_normalised CHECK (sender = lower(sender))
);
CREATE INDEX IF NOT EXISTS forward_group_sender_group_idx
    ON forward_group_sender(group_id);

-- Postfix asks one routing question for Aliases, Forwarding and Forward Groups.
-- They remain separate tables because they carry different identity/security
-- semantics. Forward Groups NEVER appear in postfix_sender_login.
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
    WHERE m.active AND d.active

    UNION ALL

    SELECT fg.address,
           fgd.destination,
           fgd.position
    FROM forward_group fg
    JOIN forward_group_destination fgd ON fgd.group_id = fg.id
    JOIN domain d ON d.id = fg.domain_id
    WHERE fg.active AND d.active;

-- Used only by the Postfix loopback policy service at RCPT time.
-- A LEFT JOIN intentionally leaves one row with sender=NULL for 'anyone' and
-- for a temporarily closed restricted group, so the policy itself remains
-- visible even when no sender is currently permitted.
CREATE OR REPLACE VIEW postfix_forward_group_policy AS
    SELECT fg.address,
           fg.sender_policy,
           fgs.sender
    FROM forward_group fg
    JOIN domain d ON d.id = fg.domain_id
    LEFT JOIN forward_group_sender fgs ON fgs.group_id = fg.id
    WHERE fg.active AND d.active;

REVOKE ALL ON forward_group, forward_group_destination, forward_group_sender,
              postfix_forward_group_policy
    FROM engine_ro_postfix, engine_ro_dovecot;

GRANT SELECT ON postfix_virtual_alias, postfix_forward_group_policy
    TO engine_ro_postfix;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON
    postfix_virtual_alias, postfix_forward_group_policy
    FROM engine_ro_postfix, engine_ro_dovecot;
