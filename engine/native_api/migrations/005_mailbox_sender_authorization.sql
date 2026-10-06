-- MateMail Native Engine — collaboration mailbox authorization.
--
-- TeamBoxes are real stored mailboxes but do not authenticate directly.
-- Members authenticate as their own personal mailbox and may be authorised to
-- use a TeamBox address as an SMTP sender. The relationship is structural here
-- rather than encoded in application-only policy, so Postfix enforces it too.

ALTER TABLE mailbox
    ADD COLUMN IF NOT EXISTS login_enabled boolean NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS mailbox_sender_authorization (
    target_mailbox_id bigint NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
    owner_mailbox_id  bigint NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mailbox_sender_authorization_pk
        PRIMARY KEY (target_mailbox_id, owner_mailbox_id),
    CONSTRAINT mailbox_sender_authorization_not_self
        CHECK (target_mailbox_id <> owner_mailbox_id)
);

CREATE INDEX IF NOT EXISTS mailbox_sender_authorization_owner_idx
    ON mailbox_sender_authorization(owner_mailbox_id);

-- A TeamBox is deliberately absent from direct authentication even though its
-- userdb row still exists for LMTP delivery and master-user PostBox access.
CREATE OR REPLACE VIEW dovecot_auth AS
    SELECT m.address,
           m.password_hash
    FROM mailbox m
    JOIN domain d ON d.id = m.domain_id
    WHERE m.active
      AND d.active
      AND m.login_enabled
      AND m.password_hash IS NOT NULL;

-- SMTP sender ownership now has three independent sources:
--  1. a login-enabled mailbox owns itself;
--  2. an Alias belongs to a login-enabled mailbox or to the authorised senders
--     of a TeamBox it targets;
--  3. a mailbox (notably TeamBox) may explicitly authorise other mailboxes.
CREATE OR REPLACE VIEW postfix_sender_login AS
    SELECT m.address,
           m.address AS owner
    FROM mailbox m
    JOIN domain d ON d.id = m.domain_id
    WHERE m.active AND d.active AND m.login_enabled

    UNION

    SELECT target.address,
           owner.address AS owner
    FROM mailbox_sender_authorization msa
    JOIN mailbox target ON target.id = msa.target_mailbox_id
    JOIN mailbox owner  ON owner.id = msa.owner_mailbox_id
    JOIN domain target_domain ON target_domain.id = target.domain_id
    JOIN domain owner_domain  ON owner_domain.id = owner.domain_id
    WHERE target.active
      AND owner.active
      AND target_domain.active
      AND owner_domain.active
      AND owner.login_enabled

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
      AND owner_domain.active
      AND owner_mb.login_enabled

    UNION

    SELECT a.address,
           sender_mb.address AS owner
    FROM alias a
    JOIN alias_destination ad ON ad.alias_id = a.id
    JOIN mailbox alias_target ON alias_target.id = ad.mailbox_id
    JOIN mailbox_sender_authorization msa
      ON msa.target_mailbox_id = alias_target.id
    JOIN mailbox sender_mb ON sender_mb.id = msa.owner_mailbox_id
    JOIN domain alias_domain ON alias_domain.id = a.domain_id
    JOIN domain target_domain ON target_domain.id = alias_target.domain_id
    JOIN domain sender_domain ON sender_domain.id = sender_mb.domain_id
    WHERE a.active
      AND alias_domain.active
      AND alias_target.active
      AND target_domain.active
      AND sender_mb.active
      AND sender_domain.active
      AND sender_mb.login_enabled;

REVOKE ALL ON mailbox_sender_authorization
    FROM engine_ro_postfix, engine_ro_dovecot;
REVOKE ALL ON dovecot_auth, postfix_sender_login
    FROM engine_ro_postfix, engine_ro_dovecot;

GRANT SELECT ON dovecot_auth TO engine_ro_dovecot;
GRANT SELECT ON postfix_sender_login TO engine_ro_postfix;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON dovecot_auth, postfix_sender_login
    FROM engine_ro_postfix, engine_ro_dovecot;
