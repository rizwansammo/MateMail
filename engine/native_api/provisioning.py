"""
Engine-side provisioning: the NE2 subset of `MailEngineAdapter`, implemented
against the engine database.

IDEMPOTENCY IS THE CONTRACT, NOT A COURTESY
    MateMail retries. Every `ensure_*` here is create-or-update and every
    `delete_*` treats "already absent" as success, because the caller's intent is
    a desired end state, not an event.

WHY UPSERTS RATHER THAN CHECK-THEN-INSERT
    Two identical provisioning requests can arrive at the same instant — a retry
    racing the original is the normal case, not the exotic one. `if not exists:
    insert` loses that race and raises a unique-violation to a caller that did
    nothing wrong. Every create here is `INSERT ... ON CONFLICT DO UPDATE`, so
    the database resolves the race and both callers observe success.

SEND-AS IS DERIVED FROM STRUCTURE
    An alias destination that resolves to a mailbox this engine hosts carries
    `mailbox_id`; one that does not carries NULL. Authorisation to send as the
    alias address follows from that column and from nothing else. Forwarding
    lives in a different table entirely and can never appear in the same query.
"""
from __future__ import annotations

import logging
import pathlib

import db
import dkim as dkim_lib
import passwords
import validation
from validation import ValidationError

logger = logging.getLogger("matemail.native.api.provisioning")


class NotFound(LookupError):
    """The object the caller named does not exist in the engine."""


# ── Domains ──────────────────────────────────────────────────────────────────


def ensure_domain(conn, spec: dict) -> dict:
    """
    Create or reconcile a domain, and make sure it has a DKIM key.

    The key is minted here rather than in a separate call because the adapter
    contract says so: `test_creating_a_domain_also_creates_its_dkim_key`. A
    surviving key from a previous life of this domain is ADOPTED, not replaced —
    see `_ensure_dkim_for`.
    """
    name = validation.domain_name(spec["name"])
    selector = validation.selector(spec.get("dkim_selector") or "mm1")
    default_mb = validation.quota_mb(spec.get("default_quota_mb", 1024), "default_quota_mb")
    max_mb = validation.quota_mb(spec.get("max_quota_mb", 1024), "max_quota_mb")
    total_mb = validation.quota_mb(spec.get("total_quota_mb", 10240), "total_quota_mb")

    # The same coherence rule DomainSpec enforces in MateMail. Checked here too
    # because the API is reachable by anything holding the secret, not only by
    # the adapter.
    if default_mb > max_mb:
        raise ValidationError(
            f"default_quota_mb ({default_mb}) exceeds max_quota_mb ({max_mb})",
            "default_quota_mb",
        )
    if max_mb > total_mb:
        raise ValidationError(
            f"max_quota_mb ({max_mb}) exceeds total_quota_mb ({total_mb})", "max_quota_mb"
        )

    active = validation.boolean(spec.get("active", True), "active")
    max_mailboxes = spec.get("max_mailboxes", 10)
    max_aliases = spec.get("max_aliases", 400)
    for label, value in (("max_mailboxes", max_mailboxes), ("max_aliases", max_aliases)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValidationError(f"{label} must be a non-negative whole number", label)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO domain (name, active, dkim_selector, max_mailboxes,
                                max_aliases, default_quota_mb, max_quota_mb,
                                total_quota_mb)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                active           = EXCLUDED.active,
                dkim_selector    = EXCLUDED.dkim_selector,
                max_mailboxes    = EXCLUDED.max_mailboxes,
                max_aliases      = EXCLUDED.max_aliases,
                default_quota_mb = EXCLUDED.default_quota_mb,
                max_quota_mb     = EXCLUDED.max_quota_mb,
                total_quota_mb   = EXCLUDED.total_quota_mb,
                updated_at       = now()
            RETURNING id
            """,
            (name, active, selector, max_mailboxes, max_aliases, default_mb, max_mb, total_mb),
        )
        cur.fetchone()

    _ensure_dkim_for(conn, name, selector)
    return {"name": name}


def set_domain_active(conn, name: str, active: bool) -> None:
    name = validation.domain_name(name)
    active = validation.boolean(active, "active")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE domain SET active = %s, updated_at = now() WHERE name = %s",
            (active, name),
        )
        if cur.rowcount == 0:
            raise NotFound(f"domain {name} is not provisioned in the engine")


def delete_domain(conn, name: str) -> None:
    """
    Remove a domain and everything that hangs off it — but NOT its DKIM key.

    That omission is deliberate and is pinned by
    `test_deleting_a_domain_does_not_delete_its_dkim_key`. MateMail's
    `remove_domain_from_engine` task deletes the key explicitly and
    unconditionally BEFORE calling this, precisely because the key outlives the
    domain. Cascading the key here would look tidier, break the contract test,
    and quietly remove the step that stops the next owner of a domain inheriting
    the previous owner's signing key.
    """
    name = validation.domain_name(name)
    with conn.cursor() as cur:
        # Mailboxes, aliases, their destinations and forwarding all cascade from
        # the domain row. DKIM does not reference it, so it survives.
        cur.execute("DELETE FROM domain WHERE name = %s", (name,))
        if cur.rowcount:
            logger.info("removed domain %s (its DKIM key is retained by design)", name)


def list_domains(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT name, active FROM domain ORDER BY name")
        return [{"name": row[0], "active": row[1]} for row in cur.fetchall()]


def _domain_id(cur, name: str) -> int:
    cur.execute("SELECT id FROM domain WHERE name = %s", (name,))
    row = cur.fetchone()
    if row is None:
        raise NotFound(f"domain {name} is not provisioned in the engine")
    return row[0]


# ── Mailboxes ────────────────────────────────────────────────────────────────


def ensure_mailbox(conn, spec: dict, password: str = "") -> dict:
    """
    Create or reconcile a mailbox.

    An empty password on a retry MUST NOT clear a working credential
    (`test_ensure_mailbox_retry_without_password_keeps_credential`), so the hash
    column is only touched when a password was actually supplied. Creating a
    mailbox with no password at all is refused — a mailbox nobody can log into
    is not a successful provision.
    """
    address = validation.email_address(spec["address"])
    local_part, _, domain = address.rpartition("@")
    quota = validation.quota_mb(spec.get("quota_mb", 1024))
    active = validation.boolean(spec.get("active", True), "active")
    display_name = spec.get("display_name", "") or ""
    if not isinstance(display_name, str):
        raise ValidationError("display_name must be a string", "display_name")

    declared = spec.get("domain")
    if declared is not None and validation.domain_name(declared, "domain") != domain:
        raise ValidationError(
            f"address {address} does not belong to domain {declared}", "domain"
        )

    password_hash = passwords.hash_password(password) if password else None

    with conn.cursor() as cur:
        domain_id = _domain_id(cur, domain)
        cur.execute("SELECT id FROM mailbox WHERE address = %s", (address,))
        exists = cur.fetchone() is not None
        if not exists and password_hash is None:
            raise ValidationError(
                "a new mailbox requires a password", "password"
            )

        cur.execute(
            """
            INSERT INTO mailbox (domain_id, address, local_part, display_name,
                                 password_hash, quota_mb, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (address) DO UPDATE SET
                domain_id     = EXCLUDED.domain_id,
                local_part    = EXCLUDED.local_part,
                display_name  = EXCLUDED.display_name,
                quota_mb      = EXCLUDED.quota_mb,
                active        = EXCLUDED.active,
                -- COALESCE keeps the stored credential when this call carried
                -- no password. Assigning EXCLUDED.password_hash unconditionally
                -- is what would lock every customer out on a retry.
                password_hash = COALESCE(EXCLUDED.password_hash, mailbox.password_hash),
                updated_at    = now()
            RETURNING id
            """,
            (domain_id, address, local_part, display_name, password_hash, quota, active),
        )
        mailbox_id = cur.fetchone()[0]
        # A destination that was external because no such mailbox existed is now
        # internal. Re-link so send-as authorisation follows reality rather than
        # the order operations happened to arrive in.
        _relink_destinations(cur, address, mailbox_id)
    return {"address": address}


def record_login(conn, address: str) -> bool:
    """
    Stamp a mailbox's last successful login. Returns whether a row matched.

    WHY IT IS HERE AND NOT IN THE HTTP LAYER
        Every write to engine state lives in this module, so there is one file
        to read when asking "what can change the database?". A write sitting in
        `app.py` next to its request handler is invisible to that question, and
        the deployment tests enforce the rule rather than trusting it.

    The caller is Dovecot's auth-policy hook, which reports a login it has
    ALREADY accepted. Dovecot cannot perform this update itself — it holds no
    write access (migration 003) — and that is the point: the mail path stays
    read-only and the API remains the single writer.

    An address with no matching mailbox returns False rather than raising. The
    hook is bookkeeping on a login that already succeeded; a mailbox deleted
    between authentication and this report is a race, not an error worth
    failing a customer's session over.
    """
    normalised = address.strip().lower()
    if not normalised:
        return False
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE mailbox SET last_login_at = now() WHERE address = %s",
            (normalised,),
        )
        matched = cur.rowcount > 0
    conn.commit()
    return matched


def set_mailbox_active(conn, address: str, active: bool) -> None:
    address = validation.email_address(address)
    active = validation.boolean(active, "active")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE mailbox SET active = %s, updated_at = now() WHERE address = %s",
            (active, address),
        )
        if cur.rowcount == 0:
            raise NotFound(f"mailbox {address} is not provisioned in the engine")


def set_mailbox_password(conn, address: str, password: str) -> None:
    """
    Replace a mailbox credential.

    The plaintext is hashed before the database is touched and is never bound
    into a query, a log line or an error. An empty password is refused rather
    than stored — the adapter contract raises `Rejected` for it.
    """
    address = validation.email_address(address)
    plaintext = validation.password(password)
    digest = passwords.hash_password(plaintext)
    if not passwords.is_hashed(digest):          # belt and braces before a write
        raise RuntimeError("refusing to store a credential that is not hashed")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE mailbox SET password_hash = %s, updated_at = now() WHERE address = %s",
            (digest, address),
        )
        if cur.rowcount == 0:
            raise NotFound(f"mailbox {address} is not provisioned in the engine")


def set_mailbox_quota(conn, address: str, quota: int) -> None:
    address = validation.email_address(address)
    quota = validation.quota_mb(quota)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE mailbox SET quota_mb = %s, updated_at = now() WHERE address = %s",
            (quota, address),
        )
        if cur.rowcount == 0:
            raise NotFound(f"mailbox {address} is not provisioned in the engine")


def delete_mailbox(conn, address: str) -> None:
    address = validation.email_address(address)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM mailbox WHERE address = %s", (address,))
        # Any alias that pointed here is now external — it must stop conferring
        # send-as. The FK is ON DELETE CASCADE for the destination row itself,
        # so this is about aliases whose destination text still names the
        # address; re-link resolves them to NULL.
        _relink_destinations(cur, address, None)


def list_mailboxes(conn, domain: str = "") -> list[dict]:
    with conn.cursor() as cur:
        if domain:
            name = validation.domain_name(domain)
            cur.execute(
                "SELECT m.address, m.active, m.quota_mb, m.last_login_at FROM mailbox m "
                "JOIN domain d ON d.id = m.domain_id WHERE d.name = %s "
                "ORDER BY m.address",
                (name,),
            )
        else:
            cur.execute(
                "SELECT address, active, quota_mb, last_login_at FROM mailbox "
                "ORDER BY address"
            )
        # `last_login_at` is NULL until NE0.21's auth-event endpoint starts
        # recording logins in NE3. Reported as None rather than omitted, because
        # "no login recorded" is a fact the caller can act on; a missing field
        # would be indistinguishable from an older engine that never had it.
        return [
            {
                "address": r[0],
                "active": r[1],
                "quota_mb": r[2],
                "last_login_at": r[3].isoformat() if r[3] else None,
            }
            for r in cur.fetchall()
        ]


# ── Aliases ──────────────────────────────────────────────────────────────────


def _relink_destinations(cur, address: str, mailbox_id: int | None) -> None:
    """
    Keep `alias_destination.mailbox_id` true after a mailbox comes or goes.

    Without this, whether an alias confers send-as would depend on whether the
    mailbox happened to be provisioned before or after the alias — an ordering
    accident deciding an authorisation question.
    """
    cur.execute(
        "UPDATE alias_destination SET mailbox_id = %s WHERE destination = %s",
        (mailbox_id, address),
    )


def ensure_alias(conn, spec: dict) -> dict:
    """
    Create or reconcile an alias and REPLACE its destination set.

    Replace, not merge: `test_ensure_alias_replaces_destination_set` pins it, and
    merging would make it impossible to remove a destination without deleting
    the alias.
    """
    address = validation.email_address(spec["address"])
    targets = validation.destinations(spec.get("destinations", []))
    if not targets:
        raise ValidationError("an alias requires at least one destination", "destinations")
    active = validation.boolean(spec.get("active", True), "active")
    _, _, domain = address.rpartition("@")

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM domain WHERE name = %s", (domain,))
        row = cur.fetchone()
        domain_id = row[0] if row else None

        cur.execute(
            """
            INSERT INTO alias (domain_id, address, active)
            VALUES (%s, %s, %s)
            ON CONFLICT (address) DO UPDATE SET
                domain_id  = EXCLUDED.domain_id,
                active     = EXCLUDED.active,
                updated_at = now()
            RETURNING id
            """,
            (domain_id, address, active),
        )
        alias_id = cur.fetchone()[0]

        cur.execute("DELETE FROM alias_destination WHERE alias_id = %s", (alias_id,))
        for position, target in enumerate(targets):
            # The join that decides send-as. A destination with no mailbox row
            # gets NULL and therefore confers nothing.
            cur.execute("SELECT id FROM mailbox WHERE address = %s", (target,))
            found = cur.fetchone()
            cur.execute(
                "INSERT INTO alias_destination (alias_id, destination, mailbox_id, position) "
                "VALUES (%s, %s, %s, %s)",
                (alias_id, target, found[0] if found else None, position),
            )
    return {"address": address, "destinations": list(targets)}


def delete_alias(conn, address: str) -> None:
    address = validation.email_address(address)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM alias WHERE address = %s", (address,))


def get_alias(conn, address: str) -> dict | None:
    address = validation.email_address(address)
    with conn.cursor() as cur:
        cur.execute("SELECT id, active FROM alias WHERE address = %s", (address,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            "SELECT destination, mailbox_id FROM alias_destination "
            "WHERE alias_id = %s ORDER BY position",
            (row[0],),
        )
        rows = cur.fetchall()
    return {
        "address": address,
        "active": row[1],
        "destinations": [r[0] for r in rows],
        # The send-as answer, made explicit rather than left to be recomputed by
        # whoever asks next.
        "authorized_senders": [r[0] for r in rows if r[1] is not None],
    }


def authorized_send_as(conn, mailbox_address: str) -> list[str]:
    """
    Every address this mailbox may use as an envelope sender, besides itself.

    Reads ONLY alias_destination. The forwarding table is not joined, is not
    unioned, and is not reachable from this query — which is the structural
    reason forwarding cannot become a sending right.
    """
    address = validation.email_address(mailbox_address)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.address
            FROM alias_destination ad
            JOIN alias   a ON a.id = ad.alias_id
            JOIN mailbox m ON m.id = ad.mailbox_id
            WHERE m.address = %s AND a.active AND m.active
            ORDER BY a.address
            """,
            (address,),
        )
        return [r[0] for r in cur.fetchall()]


# ── Forwarding ───────────────────────────────────────────────────────────────


def ensure_forwarding(conn, spec: dict) -> dict:
    """
    Replace a mailbox's forwarding set. An empty set removes forwarding.

    Writes only to `forwarding`. It never creates an alias and never touches
    `alias_destination`, so forwarding support@x.example to an external address
    grants that address nothing at all.
    """
    address = validation.email_address(spec["mailbox_address"], "mailbox_address")
    targets = validation.destinations(spec.get("destinations", []))

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM mailbox WHERE address = %s", (address,))
        row = cur.fetchone()
        if row is None:
            raise NotFound(f"mailbox {address} is not provisioned in the engine")
        mailbox_id = row[0]

        cur.execute("DELETE FROM forwarding WHERE mailbox_id = %s", (mailbox_id,))
        for position, target in enumerate(targets):
            cur.execute(
                "INSERT INTO forwarding (mailbox_id, destination, position) "
                "VALUES (%s, %s, %s)",
                (mailbox_id, target, position),
            )
    return {"mailbox_address": address, "destinations": list(targets)}


def get_forwarding(conn, mailbox_address: str) -> list[str]:
    address = validation.email_address(mailbox_address, "mailbox_address")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.destination FROM forwarding f JOIN mailbox m ON m.id = f.mailbox_id "
            "WHERE m.address = %s ORDER BY f.position",
            (address,),
        )
        return [r[0] for r in cur.fetchall()]


# ── DKIM ─────────────────────────────────────────────────────────────────────


def _ensure_dkim_for_unlocked(conn, domain: str, selector: str) -> None:
    """
    Give a domain a key if it has none; ADOPT any key that already exists.

    Adoption is the behaviour `test_recreating_a_domain_inherits_a_surviving_key`
    pins, and it is a documented hazard rather than a feature: a domain
    re-registered by someone else inherits the previous holder's signing key.
    The remedy is `delete_dkim_key`, which MateMail's deprovisioning task calls
    before removing a domain. Silently regenerating here would hide the hazard
    and break the deprovisioning contract at the same time.

    ORDER: claim the row, THEN activate. The row is claimed with
    `ON CONFLICT DO NOTHING`, so exactly one concurrent caller wins and only the
    winner activates — the losers remove their own generation file and never
    touch the active path. A crash between the claim and the activation leaves a
    row and a generation file but no live key, which reads as "no usable key"
    (not as a wrong one) and is completed by reconciliation.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT selector, private_key_path FROM dkim_key WHERE domain_name = %s",
            (domain,),
        )
        row = cur.fetchone()

    if row is not None:
        # A row is NOT by itself proof of a usable key. Activation happens after
        # the row is claimed, so a failure between the two leaves a row, a
        # generation file, and nothing live. Returning success here — which this
        # used to do — would report a provisioned domain that cannot sign.
        existing_selector, recorded_path = row
        if dkim_lib.public_material_of(dkim_lib.key_path(domain, existing_selector)) is not None:
            return                                  # healthy: adopt it unchanged

        logger.warning(
            "DKIM row for %s has no live key; recovering before reporting success", domain
        )
        candidate = pathlib.Path(recorded_path)
        if candidate.is_file():
            dkim_lib.activate(candidate, str(dkim_lib.key_path(domain, existing_selector)))
            return

        surviving = dkim_lib.generations_for(domain, existing_selector)
        if len(surviving) == 1:
            dkim_lib.activate(surviving[0], str(dkim_lib.key_path(domain, existing_selector)))
            return

        # Never mint a replacement here. A fresh key would publish material no
        # DNS record names, so a domain that looked recovered would silently
        # fail DKIM — worse than refusing and saying why.
        raise dkim_lib.DkimError(
            f"{domain} has a DKIM row but no recoverable key "
            f"({len(surviving)} surviving generations). Rotate it deliberately "
            f"and republish DNS; this is not something to guess at."
        )

    material = dkim_lib.generate(domain, selector)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dkim_key (domain_name, selector, public_key,
                                      private_key_path, key_size)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (domain_name) DO NOTHING
                RETURNING id
                """,
                (
                    domain, material["selector"], material["public_key"],
                    material["generation_path"], material["key_size"],
                ),
            )
            won = cur.fetchone() is not None
    except Exception:
        dkim_lib.remove(material["generation_path"])
        raise

    if not won:
        # Another writer claimed the domain. Remove OUR generation file and
        # nothing else — never the active path, which by now belongs to the
        # winner. Deleting that was a real bug the concurrency test caught.
        dkim_lib.remove(material["generation_path"])
        return

    dkim_lib.activate(material["generation_path"], material["active_path"])
    dkim_lib.prune_generations(domain, material["selector"], material["generation_token"])


def get_dkim_public_key(conn, domain: str) -> dict | None:
    """
    Public DKIM material, DERIVED FROM THE KEY THAT WILL ACTUALLY SIGN.

    This is what makes a DB/filesystem mismatch unrepresentable. The row is
    metadata and a cache; the active private key file is the authority. If the
    two disagree — which a crash between activation and the row update can
    produce — the file wins and the row is corrected here, so a reader never
    receives a public key that Rspamd is not signing with.

    Returns None when there is no usable key, which is an honest absence rather
    than a stale claim.
    """
    name = validation.domain_name(domain)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT selector, public_key, private_key_path FROM dkim_key "
            "WHERE domain_name = %s AND active",
            (name,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    selector, recorded_public, recorded_path = row

    live = dkim_lib.public_material_of(dkim_lib.key_path(name, selector))
    if live is None:
        # A row exists but no usable key is live. Do not fabricate one from the
        # row: reconciliation can often repair this, and until it does the
        # honest answer is that the domain has no key.
        logger.warning("DKIM row for %s has no live key; awaiting reconciliation", name)
        return None

    if live != recorded_public:
        # The crash window that used to be documented rather than fixed. The
        # file is authoritative, so the row is brought into line.
        token = dkim_lib.active_generation_token(name, selector)
        repaired_path = (
            str(dkim_lib.generation_path(name, selector, token)) if token else recorded_path
        )
        logger.warning(
            "DKIM metadata for %s did not match the live key; reconciling to the "
            "active generation", name,
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE dkim_key SET public_key = %s, private_key_path = %s "
                "WHERE domain_name = %s",
                (live, repaired_path, name),
            )

    record_name, record_value = dkim_lib.dns_record(name, selector, live)
    return {
        "selector": selector,
        "public_key": live,
        "dns_record_name": record_name,
        "dns_record_value": record_value,
    }


def _rotate_dkim_key_unlocked(conn, domain: str, selector: str = "") -> dict:
    """
    Replace a domain's signing key with a new one, crash-consistently.

        generate an immutable new generation   (old key still live and untouched)
                    |
        ACTIVATE it atomically                 <- the single commit point
                    |
        update the row to describe it          (a cache; self-heals if lost)

    After a crash at any step the domain has exactly one usable key and the
    derived public material matches it:

        before activation  -> the OLD generation is live; the row describes it
        after  activation  -> the NEW generation is live; the row may still name
                              the old one, and `get_dkim_public_key` derives the
                              new public key and repairs the row

    If the row update FAILS as an exception rather than a crash, the activation
    is rolled back so the caller's failed rotation is a clean no-op.
    """
    name = validation.domain_name(domain)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT selector, private_key_path FROM dkim_key WHERE domain_name = %s",
            (name,),
        )
        existing = cur.fetchone()

    chosen = validation.selector(selector) if selector else (
        existing[0] if existing else "mm1"
    )
    previous_selector = existing[0] if existing else None
    previous_token = (
        dkim_lib.active_generation_token(name, previous_selector) if previous_selector else None
    )

    material = dkim_lib.generate(name, chosen)

    # A SELECTOR CHANGE RETIRES THE OUTGOING KEY, and the marker is written
    # BEFORE the new key is activated.
    #
    # Rspamd re-reads `selectors.map` on its own watch cycle (up to
    # `map_watch_interval`, 300s by default). Deleting the old key the moment the
    # map changed left a window where a worker still holding the previous map
    # looked for a key that no longer existed and sent the message UNSIGNED.
    #
    # Writing the marker first is what makes a crash safe in the only direction
    # that matters: a crash after this point leaves the old key present AND
    # recorded as retired, so reconciliation keeps it for the grace period rather
    # than sweeping it as an orphan. The reverse order could lose the record of
    # why a key is still there, and delete a key Rspamd is about to use.
    retiring = bool(previous_selector and previous_selector != chosen)
    if retiring:
        dkim_lib.retire(name, previous_selector)

    # Nothing is live yet, so a failure here costs only the new generation file.
    try:
        dkim_lib.activate(material["generation_path"], material["active_path"])
    except Exception:
        dkim_lib.remove(material["generation_path"])
        if retiring:
            # The rotation never happened, so the old selector is authoritative
            # again and must not carry a retirement it will later act on.
            dkim_lib.unretire(name, previous_selector)
        raise

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dkim_key (domain_name, selector, public_key,
                                      private_key_path, key_size, rotated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (domain_name) DO UPDATE SET
                    selector         = EXCLUDED.selector,
                    public_key       = EXCLUDED.public_key,
                    private_key_path = EXCLUDED.private_key_path,
                    key_size         = EXCLUDED.key_size,
                    active           = true,
                    rotated_at       = now()
                """,
                (
                    name, material["selector"], material["public_key"],
                    material["generation_path"], material["key_size"],
                ),
            )
    except Exception:
        # A HANDLED failure must be a clean no-op: the caller got an exception,
        # so nothing it asked for may have happened. Undoing the activation
        # depends on what activation actually disturbed.
        #
        # A CRASH here is a different thing and is deliberately not handled the
        # same way: the new key is already live, the public key is derived from
        # it, and startup reconciliation settles the row. That is safe. What is
        # NOT safe is leaving a live key under a selector the database does not
        # name, which is exactly what this used to do on a selector change.
        if previous_selector == chosen:
            # Activation overwrote the live path. Put the previous generation
            # back under the same name.
            if previous_token:
                previous_file = dkim_lib.generation_path(name, previous_selector, previous_token)
                if previous_file.is_file():
                    dkim_lib.activate(
                        previous_file, str(dkim_lib.key_path(name, previous_selector))
                    )
        else:
            # Activation created a NEW active path under a selector nothing
            # refers to — either a selector change (the old one is untouched and
            # still live) or a first key for a domain that had none. Either way
            # the new path is a signing key no row accounts for, and leaving it
            # is how a stale key survives a failed call.
            dkim_lib.remove(material["active_path"])
        dkim_lib.remove(material["generation_path"])
        if retiring:
            # Same reasoning as above: a handled failure is a clean no-op, so the
            # previous selector goes back to being simply the live one.
            dkim_lib.unretire(name, previous_selector)
        raise

    # Only now is anything else unreferenced.
    dkim_lib.prune_generations(name, material["selector"], material["generation_token"])
    if retiring:
        # The old ACTIVE key deliberately stays: a stale Rspamd worker will open
        # it by name and produce a signature that still verifies against the DNS
        # record for the old selector. Reconciliation removes it once the grace
        # period has passed.
        #
        # Its GENERATIONS go now. They were only ever the immutable source for
        # the activation, nothing opens them by name, and keeping them would
        # leave key material around with no rule deciding its lifetime.
        dkim_lib.prune_generations(name, previous_selector, None)

    return {
        "selector": material["selector"],
        "public_key": material["public_key"],
        "dns_record_name": material["dns_record_name"],
        "dns_record_value": material["dns_record_value"],
    }


def _delete_dkim_key_unlocked(conn, domain: str) -> None:
    """
    Remove a domain's key and its metadata. Idempotent.

    The row goes first: if the process dies between the two, the result is key
    files with no row, which are inert and are removed by reconciliation. The
    reverse order could leave a row promising a signing capability whose key is
    already gone.
    """
    name = validation.domain_name(domain)
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM dkim_key WHERE domain_name = %s RETURNING selector",
            (name,),
        )
        row = cur.fetchone()
    if not row:
        return
    selector = row[0]
    dkim_lib.remove(dkim_lib.key_path(name, selector))
    dkim_lib.prune_generations(name, selector, None)

    # Retired keys from earlier rotations go too. They exist to keep a stale
    # Rspamd worker signing for a domain the engine still serves; once the domain
    # has no DKIM at all there is nothing left for them to sign, and leaving
    # private key material behind after an explicit delete is the wrong default.
    for retired_domain, retired_selector in dkim_lib.retired_selectors(name):
        dkim_lib.remove_retired(retired_domain, retired_selector)

    logger.info("deleted DKIM key for %s", name)


def _reconcile_dkim_unlocked(conn) -> dict:
    """
    Bring the key store and the database into agreement. Safe to run anytime.

    Runs at API startup and is exposed through readiness. It repairs exactly the
    states a crash can leave, and it is idempotent, so running it on a healthy
    engine changes nothing.

        row with no live key      -> activate the generation the row names, if it
                                     still exists; otherwise report it, because a
                                     domain with a row and no key needs an
                                     operator, not a silently minted new key that
                                     would not match published DNS
        row disagreeing with live -> correct the row from the live key
        key files with no row     -> remove; nothing refers to them
        interrupted temp files    -> remove

    It deliberately never GENERATES a key. Minting one here would publish a
    public key that no DNS record names, turning a recoverable inconsistency
    into mail that silently fails DKIM.
    """
    report = {"activated": [], "metadata_repaired": [], "orphans_removed": [],
              "staging_removed": [], "unrecoverable": [],
              "retired_retained": [], "retired_removed": []}

    with conn.cursor() as cur:
        cur.execute("SELECT domain_name, selector, public_key, private_key_path FROM dkim_key")
        rows = cur.fetchall()

    known = {}
    for domain, selector, recorded_public, recorded_path in rows:
        known[(domain, selector)] = True
        live = dkim_lib.public_material_of(dkim_lib.key_path(domain, selector))

        if live is None:
            candidate = pathlib.Path(recorded_path)
            if candidate.is_file():
                dkim_lib.activate(candidate, str(dkim_lib.key_path(domain, selector)))
                live = dkim_lib.public_material_of(dkim_lib.key_path(domain, selector))
                report["activated"].append(domain)
                logger.warning("reconciled %s: activated the generation its row names", domain)
            else:
                surviving = dkim_lib.generations_for(domain, selector)
                if len(surviving) == 1:
                    # Exactly one candidate, so activating it is a deduction
                    # rather than a choice. With several, picking one would be a
                    # guess about which public key was published — and the wrong
                    # guess signs mail that fails DKIM, which is worse than
                    # having no key and saying so.
                    dkim_lib.activate(surviving[0], str(dkim_lib.key_path(domain, selector)))
                    live = dkim_lib.public_material_of(dkim_lib.key_path(domain, selector))
                    report["activated"].append(domain)
                    logger.warning("reconciled %s: activated the only surviving generation", domain)
                elif len(surviving) > 1:
                    report["unrecoverable"].append(domain)
                    logger.error(
                        "DKIM for %s has %d surviving generations and no live key. "
                        "Which one was published is not something to guess at — "
                        "rotate deliberately and republish DNS.", domain, len(surviving),
                    )
                    continue
                else:
                    report["unrecoverable"].append(domain)
                    logger.error(
                        "DKIM for %s has a row but no key file anywhere. Rotate it "
                        "deliberately and republish DNS — this is not something to "
                        "guess at.", domain,
                    )
                    continue

        if live and live != recorded_public:
            token = dkim_lib.active_generation_token(domain, selector)
            repaired = (str(dkim_lib.generation_path(domain, selector, token))
                        if token else recorded_path)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE dkim_key SET public_key = %s, private_key_path = %s "
                    "WHERE domain_name = %s",
                    (live, repaired, domain),
                )
            report["metadata_repaired"].append(domain)
            logger.warning("reconciled %s: row now describes the live key", domain)

        # A crash between retiring the outgoing selector and committing the new
        # row can leave the marker on the selector the database still names. It
        # is authoritative, so the marker must go — left in place it would expire
        # and delete the key the engine is actively signing with.
        if dkim_lib.unretire(domain, selector):
            logger.warning(
                "reconciled %s: cleared a retirement marker from its live selector %s",
                domain, selector,
            )

        token = dkim_lib.active_generation_token(domain, selector)
        dkim_lib.prune_generations(domain, selector, token)

    # Key files the database does not name. Three different things live here and
    # the difference matters:
    #
    #   retired, inside its grace   KEEP. A stale Rspamd worker may still open
    #                               this by name, and deleting it is exactly the
    #                               unsigned window the retirement model exists
    #                               to close.
    #   retired, grace expired      remove, with its marker
    #   no marker at all            an orphan; nothing refers to it
    for path in sorted(dkim_lib.KEY_DIR.glob("*.key")):
        parsed = dkim_lib.parse_generation(path.name)
        if parsed:
            key = (parsed["domain"], parsed["selector"])
        else:
            stem = path.name[: -len(".key")]
            domain, _, selector = stem.rpartition(".")
            key = (domain, selector)
        if key in known:
            continue

        domain, selector = key
        # Only a non-generation file can be retired: generations are never opened
        # by Rspamd, so they are swept as before.
        if not parsed and dkim_lib.retired_at(domain, selector) is not None:
            if dkim_lib.retirement_expired(domain, selector):
                if dkim_lib.remove_retired(domain, selector):
                    report["retired_removed"].append(path.name)
            else:
                report["retired_retained"].append(path.name)
            continue

        if dkim_lib.remove(path):
            report["orphans_removed"].append(path.name)

    # Markers with no key left beside them describe nothing.
    for marker_domain, marker_selector in dkim_lib.retired_selectors():
        if (marker_domain, marker_selector) in known:
            continue
        if not dkim_lib.key_path(marker_domain, marker_selector).is_file():
            dkim_lib.unretire(marker_domain, marker_selector)

    for stale in dkim_lib.stale_staging_files():
        if dkim_lib.remove(stale):
            report["staging_removed"].append(stale.name)

    if any(report[k] for k in ("activated", "metadata_repaired", "orphans_removed",
                               "staging_removed", "unrecoverable", "retired_removed")):
        logger.warning("DKIM reconciliation made changes: %s", report)
    return report


# ── the DKIM lifecycle is serialised ────────────────────────────────────────
#
# Every mutation of a key — creation, rotation, deletion and reconciliation —
# runs under one PostgreSQL advisory lock. Two rotations of the same domain
# could otherwise interleave:
#
#     A generates -> B generates -> A activates -> B activates
#     -> B writes the row and returns B -> A writes the row and returns A
#
# A later read repairs the row, but the damage is already done: caller A was
# handed public material that is not the signing key. Returning a key that was
# never live is not something a subsequent repair can undo, because the caller
# has already published it.
#
# Holding the lock across the WHOLE filesystem-and-database lifecycle means a
# successful response always describes the generation that operation committed,
# and reconciliation can never sweep a rotation's files out from under it.


def _publish_selector_map(conn) -> None:
    """
    Republish the domain-to-selector map Rspamd signs from.

    WHY IT LIVES IN THE LOCK WRAPPERS
        Every DKIM lifecycle change passes through one of the four functions
        below, so this is the one place that sees all of them. Publishing here,
        still holding the lifecycle lock, means the map is derived from rows
        that are already committed and cannot interleave with a concurrent
        rotation writing a different selector for the same domain.

    WHY A FAILURE PROPAGATES
        It would be easy to log and continue — the key is already written and
        the row already committed, so the operation "worked". But a domain whose
        selector never reached the map is a domain whose mail goes out UNSIGNED,
        and reporting success for that is exactly the kind of quiet half-failure
        the engine is supposed to make impossible. The caller retries; every one
        of these operations is idempotent, and `reconcile_dkim` republishes the
        map at startup regardless.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT domain_name, selector FROM dkim_key ORDER BY domain_name")
        pairs = [(row[0], row[1]) for row in cur.fetchall()]
    dkim_lib.write_selector_map(pairs)


def _ensure_dkim_for(conn, domain: str, selector: str) -> None:
    with db.dkim_lifecycle_lock(conn):
        result = _ensure_dkim_for_unlocked(conn, domain, selector)
        _publish_selector_map(conn)
        return result


def rotate_dkim_key(conn, domain: str, selector: str = "") -> dict:
    with db.dkim_lifecycle_lock(conn):
        result = _rotate_dkim_key_unlocked(conn, domain, selector)
        _publish_selector_map(conn)
        return result


def delete_dkim_key(conn, domain: str) -> None:
    with db.dkim_lifecycle_lock(conn):
        result = _delete_dkim_key_unlocked(conn, domain)
        _publish_selector_map(conn)
        return result


def reconcile_dkim(conn) -> dict:
    with db.dkim_lifecycle_lock(conn):
        result = _reconcile_dkim_unlocked(conn)
        _publish_selector_map(conn)
        return result
