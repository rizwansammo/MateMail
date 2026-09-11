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

## Engine runtime as installed (P4B)

The engine is installed and validated on MateServer. It is **not yet activated**
— production MateMail still runs `MAIL_ENGINE_ADAPTER=stub`.

| | |
|---|---|
| Release | `2026-07b`, commit `02552ffefdf0869f988edf4a7e03822e8b467b34` |
| Install path | `/opt/mailcow-dockerized` |
| Hostname | `mx.matemail.online` (Let's Encrypt certificate, host Certbot) |
| Engine network | `10.244.0.0/24` — chosen to avoid mailcow's default `172.22.1.0/24`, which collides with `portfolio_default` on this host |
| IPv6 | disabled |
| ClamAV / SOGo / FTS | all enabled |
| `network_mode: host` | used by `netfilter-mailcow` only — the single approved upstream exception. No application or mail service uses host networking. |

### The private path from MateMail to the engine

```
backend / celery-worker
    │        (and only those two — nothing else joins the link)
    ▼
matemail_engine_link          dedicated, external, internal: true
    │  mx.matemail.online  →  the gateway's alias on this network
    ▼
matemail-engine-gateway       HAProxy 3.2 LTS, TCP passthrough only
    │  10.244.0.247           pinned identity on the engine's network
    ▼
nginx-mailcow:8453            engine HTTPS API
postfix-mailcow:587           SMTP submission, STARTTLS
```

No host port is published anywhere on this path, and no address appears in
MateMail's configuration: `MAIL_ENGINE_API_URL` and `EMAIL_HOST` name
`mx.matemail.online`, the link network's alias resolves it, and the certificate
validates because the name is real.

**Why one gateway rather than two aliases.** Attaching `nginx-mailcow` and
`postfix-mailcow` to the link under the same alias would let Docker's DNS return
either address, while 8453 exists only on one and 587 only on the other — a
routing coin-flip. One ingress owning the name makes the destination
deterministic.

**Why TCP passthrough and not a proxy that understands the protocols.** The
gateway is layer 4. It does not terminate, inspect or re-originate TLS, and it
is not an SMTP relay. The certificate MateMail validates is the real one
presented by nginx and by Postfix, so hostname verification stays meaningful end
to end and the gateway never holds key material or parses a mail command.

**Why not nginx-mailcow itself.** Its image has nginx's stream module compiled
in, but there is no update-safe place to put a `stream {}` block: the only
writable, bind-mounted nginx path is `/etc/nginx/conf.d`, which upstream
includes *inside* `http {}` where `stream {}` is invalid, and the one file with
a top-level context — `nginx.conf.j2` — is tracked in mailcow's git and restored
by `update.sh`.

The gateway is defined in mailcow's `docker-compose.override.yml`, which is
listed in mailcow's own `.gitignore` and is the supported extension point.
Upstream's `docker-compose.yml` is never edited.

### Why binding to a Docker bridge gateway address was rejected

This is worth keeping, because the rejected design looks correct and is what
most guidance suggests.

The engine originally published its API and submission ports on MateMail's own
app-network gateway, `172.24.0.1`, on the reasoning that a private bridge
address is private. It is not. **A published bind address selects a destination
address; it never restricts the source.** Measured on this host, containers on
`netamate_internal`, `matedesk_internal`, `mateassist_internal`,
`mateconnect_internal`, `portfolio_default` and the default bridge all opened
TCP connections to both ports.

The mechanism, confirmed by rule counters rather than inferred: `docker-proxy`
holds a userland socket on that address and accepts on it whichever bridge the
traffic arrived from. During the test the DNAT rule did not fire at all while
the filter ACCEPT did, once per connection — so the traffic never traverses
`DOCKER-USER`, and a rule there would not have helped. Filtering would have had
to happen in `INPUT`, a host firewall change, and the socket would still be
published.

The same re-origination is why the engine saw `10.244.0.1` as the client for
every request regardless of who sent it, which made its own `allow_from` ACL
unable to identify the caller. **One root cause, two symptoms**, and both
disappear when the socket does.

---

## Mailcow REST API Endpoints Used by MateMail

> **Wire contract.** Verified against `2026-07b`'s OpenAPI specification, its
> `json_api.php` router, and the live engine. Three shapes are easy to get wrong
> and fail only against a real engine:
>
> - every `/delete/` endpoint **rejects non-POST with HTTP 405**;
> - `/delete/*` takes a **bare JSON array**, not an object — the router assigns
>   the whole request body to `$_POST['items']`;
> - `/edit/*` takes `{"items": [...], "attr": {...}}`.
>
> `set/quarantine/release` **does not exist** at this version; the adapter
> raises `EngineCapabilityMissing` rather than silently reporting success.

```
Base URL: https://mx.matemail.online:8453   (private; see the path above)
Auth: X-API-Key: <issued by the engine, never in the repository>

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

## DKIM lifecycle

**Verified against the running engine in P4B, not inferred.** Four of MateMail's
assumptions about this lifecycle were wrong, and every one of them looked
reasonable in code review. The four facts that matter:

| Fact | Consequence for MateMail |
|---|---|
| `add/domain` **generates the keypair**, using the `dkim_selector` and `key_size` sent on that same call | Those fields must be on the domain-create request. There is no second chance. |
| `add/dkim` is **refused** while a key already exists (`dkim_domain_or_sel_invalid`, `functions.dkim.inc.php:21`) | A bare `add/dkim` is not rotation. It is a guaranteed failure. |
| `delete/domain` does **not** delete the key | Deprovisioning must delete it explicitly, or the next owner of the domain inherits it. |
| `delete/dkim` is the only thing that removes a key, and is idempotent | It is the remedy, and it is safe to call unconditionally. |

### Provisioning

1. The customer adds a domain in MateMail and proves ownership (P3a).
2. `ensure_domain` sends `POST /api/v1/add/domain` **including `dkim_selector`
   and `key_size`**. The engine creates the domain and mints the keypair,
   keeping the private half (DEC-007r).
3. `_adopt_engine_dkim` **reads** the key with `GET /api/v1/get/dkim/{domain}`.
   It does not generate one — generation only runs in the recovery case where
   the engine somehow holds none.
4. The public material is stored in `Domain.dkim_public_key` and shown as a DNS
   TXT record. The selector recorded is **the one the engine reports**, never
   the one MateMail asked for: the engine is what signs, so if the two ever
   disagree the engine wins.
5. The customer publishes the record; MateMail verifies it by DNS lookup.

Omitting the selector in step 2 does not skip generation — it makes the engine
use its own default, `dkim`. MateMail would then publish `mm1._domainkey` for a
key signed under `dkim._domainkey`, and every outgoing message would fail DKIM
with nothing in MateMail reporting a problem.

### Rotation

```
read current key → delete/dkim (only if one exists) → add/dkim → read back
```

The conditional delete is what makes rotation retry-safe. After a rotation that
deleted the old key and then failed to add the new one, the engine holds
nothing; deleting unconditionally would be harmless there but would destroy the
*replacement* key on any later retry that ran after a successful add.

Rotation is **not idempotent** and deliberately **not a Celery task** — it
invalidates the DNS record the customer has published, and a retry would mint
another key and leave DNS permanently stale. It lives in
`apps.mail_engine.services.rotate_domain_dkim`, is called synchronously, and is
serialized per domain with `select_for_update` so two concurrent rotations
cannot destroy one another's keys.

### Deprovisioning — a security operation

```
set_domain_active(False) → delete_dkim_key → delete_domain
```

Measured in P4B: tenant A registers a domain and is issued a key; tenant A
removes the domain; the domain record disappears but the key remains; tenant B
later registers the same domain and **inherits tenant A's private signing key**,
along with tenant A's selector rather than the one tenant B asked for.

Domains legitimately change hands, so this is not a hypothetical. The key
deletion is therefore unconditional and runs **even when the domain is already
absent** — no domain plus stale key is exactly the state a previously failed
cleanup leaves behind, and short-circuiting on "domain not found" would make the
cleanup permanently unable to repair itself.

A transient failure retries. A terminal failure **raises** rather than returning
quietly, because a silent return would report clean removal over a usable
signing key for a domain MateMail no longer controls.

### Private key exposure

The engine returns a `privkey` field on every DKIM read. It is empty while
`$SHOW_DKIM_PRIV_KEYS` is `false`, which P4B verified as the effective value
inside the running php-fpm container.

That is a **PHP variable, not a `mailcow.conf` setting** — an earlier version of
this documentation said otherwise, and configuring it there would have had no
effect at all:

| | |
|---|---|
| Upstream default | `data/web/inc/vars.inc.php` (ships `false`) |
| Persistent override | `data/web/inc/vars.local.inc.php` |
| Required effective value | `$SHOW_DKIM_PRIV_KEYS = false;` |

The adapter strips private fields unconditionally regardless, and `DkimKeyInfo`
has no field able to hold one. See `MAIL_ENGINE_P4A.md` §6 for the three layers.

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
