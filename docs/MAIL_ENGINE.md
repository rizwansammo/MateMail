# MAIL_ENGINE.md — MateMail Mail Engine

**Product:** MateMail
**Last updated:** 2026-09-10
**Status:** Re-framed by DEC-011 — engine selection unchanged, product framing changed

---

## What this document is

This is the internal engineering guide to the **MateMail Mail Engine**: the
Postfix / Dovecot / Rspamd stack, orchestrated via mailcow, that MateMail uses
to actually move and store mail.

**Framing (DEC-011, 2026-09-10).** The engine is an *internal component of
MateMail*, not a separate product MateMail administers. Customers must never
learn it exists. Everything below the `MailEngineAdapter` is implementation
detail; everything customers touch is MateMail's own UI and API.

The option comparison below is retained for history — it explains why this stack
was chosen. Read `docs/ARCHITECTURE.md` v2.0 first for the current model, and
`PROJECT_STATUS.md` → *Mail Engine integration review* for the current state of
the boundary, including known leaks.

> **Note on the section headings that follow.** They refer to "Mailcow" because
> that is the upstream software. In code, docs, UI and customer communication,
> the component is called the **MateMail Mail Engine**.

---

## Repository Review (historical — May 2026)

At the time of the original review the repository was empty, so the engine choice
was made from scratch with no existing work to preserve.

---

## Options Evaluated

### Option A — Stalwart Mail Server (all-in-one)

**What it is:** A modern Rust-based all-in-one mail server. One binary handles SMTP, IMAP, JMAP, spam filtering, and DKIM.

**Pros:**
- Single container, simple ops
- REST management API built-in
- JMAP protocol enables easier webmail integration
- Fast, modern, actively developed
- DKIM signing built-in
- PostgreSQL backend supported

**Cons:**
- Younger project (less production mileage than mailcow)
- Smaller community and ecosystem
- Spam filtering less mature than Rspamd
- No pre-built SaaS management integration pattern
- Webmail API via JMAP is non-standard in the ecosystem

**Verdict for MateMail:** Viable but younger/smaller ecosystem. Better as a future migration target than MVP foundation.

---

### Option B — Mailcow-Style Backend Foundation (Recommended)

**What it is:** The mailcow project is a complete, production-proven mail server suite built on:
- Postfix (SMTP inbound + submission)
- Dovecot (IMAP/POP3/auth)
- Rspamd (spam filtering + DKIM signing)
- ClamAV (antivirus, optional)
- Redis (Rspamd + mailcow internal)
- MariaDB (mailcow's own internal database)
- Nginx (mailcow internal routing)
- SoGo (mailcow's built-in webmail — NOT exposed to MateMail customers)

Mailcow exposes a mature REST API at `/api/v1/` for managing all objects: domains, mailboxes, aliases, forwards, spam policies, DKIM, TLS settings, queue, and more.

**Integration model for MateMail:**
1. Mailcow runs in its own Docker Compose service group.
2. MateMail Django control plane calls mailcow REST API for all provisioning.
3. Mailcow handles all SMTP/IMAP/auth/spam at the protocol level.
4. MateMail UI completely replaces mailcow's UI for all customer-facing interactions.
5. Mailcow admin panel is internal-only (firewalled, platform staff only).
6. SoGo (mailcow's built-in webmail) is NOT exposed to MateMail customers. MateMail's own Next.js webmail is the customer interface.

**Pros:**
- Battle-tested, widely deployed in production
- Comprehensive REST API — every object manageable via API
- Postfix + Dovecot + Rspamd are the gold standard for mail infrastructure
- Large community, extensive documentation
- DKIM via Rspamd (mature, reliable)
- Spam filtering via Rspamd (best-in-class open source)
- Anti-virus integration (ClamAV)
- Well-understood failure modes

**Cons:**
- More containers (~10 for mailcow's own stack)
- Mailcow uses MariaDB internally — MateMail uses PostgreSQL for its own data (two database engines)
- Higher memory footprint (~2-3 GB minimum for the mailcow stack alone)
- Mailcow is opinionated — don't fight its config structure
- Platform staff must maintain two systems (MateMail + mailcow stack)

**How to manage the dual-database situation:**
- PostgreSQL: MateMail control plane (tenants, users, billing, logs, DNS, RBAC)
- MariaDB: mailcow internal data (mail domains, mailbox passwords, aliases, forwards)
- These are deliberately separate. MateMail Django writes to its own PostgreSQL, then calls mailcow API to sync state.
- Django is the source of truth for business data. Mailcow is the source of truth for mail delivery state.
- If they ever diverge, a Celery sync task reconciles them.

---

### Option C — Custom Postfix + Dovecot Stack (from scratch)

**What it is:** Build a custom mail stack using Postfix + Dovecot + Rspamd + OpenDKIM, driven by PostgreSQL virtual maps.

**Pros:**
- Full control
- No dependency on a third-party mail management project
- Can use a single PostgreSQL database for both MateMail and mail engine

**Cons:**
- Very complex to build correctly (dozens of config files, virtual maps, auth chains)
- Easy to accidentally create an open relay
- High expertise requirement for correct Postfix/Dovecot configuration
- Long build time before anything works end-to-end
- Not realistic for a single-team MVP

**Verdict:** Not recommended for MVP. Feasible only if Option B proves too constrained.

---

## Final Recommendation: Option B — Mailcow Foundation

**Reasoning:**

1. The repository is empty. There is no existing work to preserve or migrate.
2. Mailcow's REST API maps directly to MateMail's provisioning needs (domains, mailboxes, aliases, forwards, DKIM, spam, queue).
3. Postfix + Dovecot + Rspamd is the most battle-tested open source mail stack in existence.
4. The separation of concerns is clean: mailcow handles mail protocol, MateMail handles SaaS.
5. The dual-database concern is manageable — it is a well-understood pattern in platform-over-engine architectures.
6. Mailcow's admin UI is not exposed to customers. MateMail's Next.js frontend is the only customer interface.
7. When scale demands it, mailcow's components can be replaced incrementally (e.g., Stalwart can replace the entire mailcow stack later) without changing the MateMail control plane adapter interface.

**What MateMail does NOT do:**
- Modify mailcow's Postfix, Dovecot, or Rspamd configs directly
- Expose mailcow's admin panel to customers
- Expose mailcow, Postfix, Dovecot, Rspamd, or any engine name in the customer UI
- Write directly to mailcow's MariaDB (all changes via mailcow API)

---

## Mailcow REST API Endpoints Used by MateMail

```
Base URL: http://mailcow:8080 (internal Docker network only)
Auth: X-API-Key: <internal secret>

# Domain management
GET    /api/v1/get/domain/all
POST   /api/v1/add/domain
POST   /api/v1/edit/domain
POST   /api/v1/delete/domain

# Mailbox management
GET    /api/v1/get/mailbox/all
POST   /api/v1/add/mailbox
POST   /api/v1/edit/mailbox
POST   /api/v1/delete/mailbox

# Alias management
GET    /api/v1/get/alias/all
POST   /api/v1/add/alias
POST   /api/v1/edit/alias
POST   /api/v1/delete/alias

# DKIM management
GET    /api/v1/get/dkim/{domain}
POST   /api/v1/add/dkim
POST   /api/v1/edit/dkim
POST   /api/v1/delete/dkim

# Queue management
GET    /api/v1/get/mailq/all
POST   /api/v1/edit/mailq   (flush/delete)

# Spam policy
POST   /api/v1/add/spampolicy/
POST   /api/v1/edit/spampolicy/

# Sync status
GET    /api/v1/get/status/containers
```

---

## Django → Mailcow Adapter Interface

The `mail_engine` service layer in Django wraps all mailcow API calls. No other Django code touches mailcow directly.

```python
# backend/mail_engine/adapter.py

class MailEngineAdapter:
    def provision_domain(self, domain: Domain) -> ProvisionResult: ...
    def suspend_domain(self, domain: Domain) -> ProvisionResult: ...
    def delete_domain(self, domain: Domain) -> ProvisionResult: ...
    
    def provision_mailbox(self, mailbox: Mailbox) -> ProvisionResult: ...
    def disable_mailbox(self, mailbox: Mailbox) -> ProvisionResult: ...
    def enable_mailbox(self, mailbox: Mailbox) -> ProvisionResult: ...
    def delete_mailbox(self, mailbox: Mailbox) -> ProvisionResult: ...
    def update_mailbox_password(self, mailbox: Mailbox, new_hash: str) -> ProvisionResult: ...
    def update_mailbox_quota(self, mailbox: Mailbox, quota_mb: int) -> ProvisionResult: ...
    
    def provision_alias(self, alias: Alias) -> ProvisionResult: ...
    def delete_alias(self, alias: Alias) -> ProvisionResult: ...
    
    def provision_forwarding(self, rule: ForwardingRule) -> ProvisionResult: ...
    def delete_forwarding(self, rule: ForwardingRule) -> ProvisionResult: ...
    
    def provision_dkim(self, domain: Domain) -> ProvisionResult: ...
    
    def get_queue_status(self, tenant: Tenant) -> list[QueueItem]: ...
    def get_quarantine_items(self, tenant: Tenant) -> list[QuarantineItem]: ...
    
    def sync_all(self, tenant: Tenant) -> SyncResult: ...
```

This interface is implementation-agnostic. A `MailcowAdapter` implements it for mailcow. A `StalwartAdapter` or `PostfixAdapter` would implement the same interface if the engine ever changes.

---

## DKIM Integration

Mailcow manages DKIM via Rspamd. MateMail workflow:

1. Admin adds domain in MateMail UI
2. Django calls `POST /api/v1/add/dkim` with domain name and key length (2048)
3. Mailcow generates keypair internally, Rspamd handles signing
4. Django calls `GET /api/v1/get/dkim/{domain}` to retrieve the public key
5. Public key is stored in `Domain.dkim_public_key` and shown to the user as a DNS TXT record
6. After user adds the record, Django verifies via DNS lookup
7. Domain status updated accordingly

---

## Webmail Integration

MateMail uses its own Next.js webmail. It does NOT use SoGo (mailcow's built-in webmail).

For reading mail, Django webmail API connects to Dovecot IMAP using Python's `imaplib` or `aioimaplib` (async) with the mailbox user's credentials.

For sending mail, Django webmail API connects to Postfix submission port (587) using `smtplib` with authenticated credentials.

This means webmail works over standard IMAP/SMTP protocols — the same protocols any external mail client uses.

---

## Manual Test Commands

### Test No Open Relay
```bash
# Must be rejected — no auth
swaks --to external@gmail.com --from nobody@random.com \
  --server smtp.matemail.online --port 587
# Expected: 530 Authentication required
```

### Test SMTP Auth
```bash
# Valid credentials — must succeed
swaks --to recipient@matemail.online \
  --from ariana@yourdomain.com \
  --server smtp.matemail.online --port 587 --tls \
  --auth-user ariana@yourdomain.com --auth-password testpass
```

### Test IMAP
```bash
openssl s_client -connect imap.matemail.online:993 -quiet
# Type: A1 LOGIN ariana@yourdomain.com testpass
# Expected: A1 OK LOGIN completed
```

### Test Unknown Domain Rejection
```bash
swaks --to user@nothosted.xyz --from test@gmail.com \
  --server mx.matemail.online --port 25
# Expected: 550 relay not permitted
```

### Test Unknown Recipient Rejection
```bash
swaks --to nobody@yourdomain.com --from test@gmail.com \
  --server mx.matemail.online --port 25
# Expected: 550 User unknown
```

---

## Resource Requirements

Mailcow minimum requirements (per mailcow documentation):
- RAM: 6 GB minimum (2 GB for mailcow stack, rest for MateMail stack)
- CPU: 2 cores minimum
- Disk: 20 GB minimum (plus mail storage)
- OS: Ubuntu 22.04 LTS or Debian 12 (mailcow official support)

MateMail additional requirements:
- RAM: ~2 GB (Django + Next.js + PostgreSQL + Redis + Celery)
- Total recommended: 8 GB RAM, 4 CPU cores, 40 GB SSD minimum

---

## Future Upgrade Path

If mailcow is replaced later:

1. Implement a new adapter class satisfying the `MailEngineAdapter` interface.
2. No other Django code changes.
3. Migration requires: exporting mail data from mailcow, importing into new engine.
4. Config: set `MAIL_ENGINE_BACKEND=stalwart` (or custom) in env.
