# DECISIONS.md — Architecture and Product Decisions

**Product:** MateMail  
**Last updated:** 2026-05-29

Each entry documents a decision, the options considered, the choice made, and the reasoning. Decisions are dated so future engineers can understand why a choice was made at a given point in time.

---

## DEC-001 — Mail Engine Selection

**Date:** 2026-05-29  
**Status:** Decided  
**Decided by:** Phase 0 architecture review

**Question:** Which mail engine should MateMail use as its underlying SMTP/IMAP infrastructure?

**Options considered:**

| Option | Description |
|--------|-------------|
| A | Stalwart Mail Server — Rust, all-in-one, REST API, JMAP |
| B | Mailcow-style foundation — Postfix + Dovecot + Rspamd, mailcow REST API |
| C | Custom Postfix + Dovecot stack — built from scratch with PostgreSQL virtual maps |

**Decision:** Option B — Mailcow-style backend foundation

**Reasoning:**
- Mailcow's REST API at `/api/v1/` supports all provisioning operations MateMail needs (domains, mailboxes, aliases, DKIM, queue, spam).
- Postfix + Dovecot + Rspamd is the most proven open source mail stack — battle-tested at scale.
- Option A (Stalwart) is younger and less proven in large production environments.
- Option C requires deep mail infrastructure expertise and a long build time.
- The repo is empty — no existing work to preserve, so this is a clean architectural choice.
- Mailcow's admin UI is suppressed for customers. MateMail's Next.js frontend is the only customer interface.

**Constraints imposed:**
- MateMail never exposes mailcow, Postfix, Dovecot, Rspamd, or any engine name in the customer UI.
- MateMail Django writes only to its own PostgreSQL, then calls mailcow API to sync.
- Mailcow's MariaDB is mailcow-internal only. MateMail does not write to it directly.
- The `MailEngineAdapter` interface is implementation-agnostic so the engine can be replaced.

**Revisit trigger:** If mailcow proves too constrained for multi-tenant quota isolation or if Stalwart matures further, revisit before Phase 6.

---

## DEC-002 — Frontend Framework

**Date:** 2026-05-29  
**Status:** Decided

**Decision:** Next.js 14 with App Router, TypeScript, Tailwind CSS

**Reasoning:**
- Required by project spec.
- App Router enables server components for faster initial loads.
- TypeScript prevents class of bugs at compile time.
- Tailwind matches the prototype's utility-class patterns exactly.

---

## DEC-003 — Backend Framework

**Date:** 2026-05-29  
**Status:** Decided

**Decision:** Django 5 + Django REST Framework + PostgreSQL

**Reasoning:**
- Required by project spec.
- Django ORM's querysets are easy to enforce tenant isolation with a custom manager.
- DRF provides permission classes, serializers, and throttling infrastructure.
- PostgreSQL is the right relational database for a multi-tenant SaaS with complex queries.

---

## DEC-004 — Authentication Model

**Date:** 2026-05-29  
**Status:** Decided

**Decision:** JWT (access + refresh) for the SaaS app; separate IMAP/SMTP credentials for mail protocols

**Reasoning:**
- JWT allows the Next.js frontend to be stateless and makes multi-subdomain auth easier (app.matemail.online and webmail.matemail.online share JWT claims).
- IMAP/SMTP auth is handled entirely by Dovecot (via mailcow) — completely separate auth path from web login.
- Web login credentials and mailbox credentials are distinct (different passwords, different auth systems). Users set a separate mailbox password.

---

## DEC-005 — Webmail Implementation

**Date:** 2026-05-29  
**Status:** Decided

**Decision:** MateMail builds its own Next.js webmail. Does NOT use SoGo (mailcow's built-in webmail).

**Reasoning:**
- SoGo has its own UI that does not match MateMail's design system.
- The project requires a custom webmail UX following the provided prototype.
- Django webmail API uses standard IMAP (`imaplib`/`aioimaplib`) for reading and SMTP (`smtplib`) for sending.
- This uses the same protocol path as any external mail client, ensuring consistency.

---

## DEC-006 — Tenant Data Isolation Strategy

**Date:** 2026-05-29  
**Status:** Decided

**Decision:** Row-level tenant scoping with a `TenantScopedManager`, not schema-per-tenant

**Reasoning:**
- Schema-per-tenant (PostgreSQL schemas) is more isolated but significantly more complex (migrations, connection pooling, query routing).
- Row-level scoping with a custom manager is the Django standard for multi-tenant SaaS.
- Every model that holds tenant-owned data has a `tenant` FK.
- A middleware resolves the current tenant from the JWT and attaches it to `request.tenant`.
- All querysets go through `TenantScopedManager` which injects `.filter(tenant=request.tenant)`.
- Tests verify isolation by attempting cross-tenant access and asserting 404/403.

---

## DEC-007 — DKIM Key Management

**Date:** 2026-05-29  
**Status:** Decided

**Decision:** Mailcow/Rspamd manages DKIM keypair generation. Django retrieves the public key via mailcow API and stores it for display.

**Reasoning:**
- Mailcow already has DKIM management built into its API (`/api/v1/add/dkim`, `/api/v1/get/dkim/{domain}`).
- Having Django generate keypairs separately and then push private keys to mailcow creates a key management complexity.
- Simpler and safer to let mailcow own the private key, Django only reads the public key for DNS display.
- DKIM private key never leaves the mailcow container.

**Revisit trigger:** If custom DKIM selector naming is required per-tenant, may need to generate keys in Django.

---

## DEC-008 — Reverse Proxy

**Date:** 2026-05-29  
**Status:** Tentatively decided (final in Phase 15)

**Decision:** Nginx as the primary reverse proxy, or Caddy as an alternative for auto-TLS

**Reasoning:**
- Nginx is the standard; most documentation and examples target it.
- Caddy has automatic TLS renewal built-in (no certbot needed) — this simplifies Phase 15.
- mailcow already includes its own Nginx for internal routing. The external reverse proxy must be careful not to conflict.
- Final decision deferred to Phase 15 deployment work.

---

## DEC-009 — Container Orchestration

**Date:** 2026-05-29  
**Status:** Decided (MVP scope)

**Decision:** Docker Compose for local development and production MVP. Kubernetes deferred.

**Reasoning:**
- Docker Compose is sufficient for a single-server MVP deployment.
- Kubernetes adds significant ops overhead not warranted at MVP stage.
- MateMail's growth path includes Kubernetes if multi-server is needed — Compose files can be translated to Helm charts later.
- mailcow also uses Docker Compose natively.

---

## DEC-010 — Async Task System

**Date:** 2026-05-29  
**Status:** Decided

**Decision:** Celery with Redis broker

**Reasoning:**
- Required by project spec.
- Redis is already required for caching; reusing it as the Celery broker avoids an extra service.
- Celery handles DNS verification, DKIM checks, mailbox provisioning retries, backup jobs, billing sync, and log ingestion.

---

## Open Decisions (to be resolved in later phases)

| ID | Question | Target phase |
|----|----------|-------------|
| TBD-A | Stripe vs. placeholder for billing | Phase 11 |
| TBD-B | Caddy vs. Nginx for production reverse proxy | Phase 15 |
| TBD-C | Mail storage volume strategy (local vs. S3) | Phase 14 |
| TBD-D | RBL/DNSBL configuration for spam | Phase 10 |
| TBD-E | ClamAV enable/disable in mailcow | Phase 10 |
| TBD-F | MTA-STS hosting strategy (HTTPS required) | Phase 5 |
