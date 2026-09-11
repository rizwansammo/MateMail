# DECISIONS.md — Architecture and Product Decisions

**Product:** MateMail  
**Last updated:** 2026-05-29

Each entry documents a decision, the options considered, the choice made, and the reasoning. Decisions are dated so future engineers can understand why a choice was made at a given point in time.

---

## DEC-001 — Mail Engine Selection

> **STILL VALID as an engine choice, but RE-FRAMED by DEC-011 (2026-09-10):** mailcow is MateMail's *internal* Mail Engine, not a separately-presented product.

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

> **SUPERSEDED by DEC-005r (2026-09-10).** Retained for history.

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

> **SUPERSEDED by DEC-007r (2026-09-10)** — but its security intent is restored, not discarded. DEC-007r keeps the private key inside the Mail Engine, and additionally assigns the customer-facing DKIM capability to MateMail. The current Django-held key is technical debt scheduled for migration.

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

> **RESOLVED (2026-09-10):** host-native nginx on MateServer, per the NetaMate production model in `CLAUDE.md`. Caddy is not used, and MateMail must not introduce a second containerized nginx for the SaaS app.

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

## DEC-011 — MateMail is one integrated product, not a control panel

**Date:** 2026-09-10
**Status:** Accepted — supersedes the framing of DEC-001 and DEC-005
**Decision maker:** NetaMate Solutions (product direction)

**Context:**
The build so far treated mailcow as a separately-presented mail product that
MateMail administered. That framing produced a control panel with gaps: features
whose UI existed while the mechanism lived only in mailcow, and a webmail plan
that depended on a third-party interface.

| Option | Description |
|---|---|
| A | Keep MateMail as a control panel; expose mailcow/SOGo for mail and webmail |
| B | Fork mailcow and rewrite it as MateMail |
| C | Use mailcow's infrastructure as MateMail's **internal Mail Engine**; MateMail owns the whole product surface |

**Decision:** Option C.

Postfix, Dovecot, Rspamd and the rest remain proper separate services — no
attempt to collapse them into one container — but they are architected and
documented as the **MateMail Mail Engine**, an internal component. mailcow is an
implementation detail, invisible to customers.

**Reasoning:**
- Option A leaves the product incomplete and the brand fragmented: customers
  would meet two different products and two different admin experiences.
- Option B discards the reason for choosing mailcow in the first place (DEC-001):
  mature, maintained, security-patched mail infrastructure. Rewriting Postfix and
  Dovecot orchestration is years of work with no product differentiation.
- Option C keeps the maturity and owns the product. MateMail owns tenancy,
  permissions, plans, quotas, onboarding, ownership verification, provisioning,
  suspension, anti-abuse, rate limiting, audit, monitoring, backups, DNS
  guidance, queue/quarantine features, webmail and all UI.

**Consequences:**
- The `MailEngineAdapter` port becomes load-bearing rather than decorative, and
  must be hardened: typed errors, no engine-shaped data crossing outward, no
  product logic inside the adapter. See the integration review in
  `PROJECT_STATUS.md`.
- Anything the engine can do that customers need must be surfaced through
  MateMail's own API and UI, or it is not a feature.
- The engine's admin UI is never published. Every routine operational task
  should eventually exist in MateMail's platform-admin UI.
- Engine identity must not leak: no branding, hostnames, container names, API
  shapes or raw engine errors in any customer-reachable surface.
- Both sides run on MateServer; there is no separate mail VPS.

**Revisit trigger:** If the engine's constraints (per-domain limits, quota model,
multi-tenancy granularity) block a committed product capability, revisit — but
replace the engine behind the adapter rather than exposing it.

---

## DEC-005r — Webmail: MateMail-built, not SOGo

**Date:** 2026-09-10
**Status:** Accepted — replaces DEC-005
**Supersedes:** DEC-005 (custom Next.js webmail over IMAP), which was never built

**Decision:** The long-term product remains a MateMail-built webmail experience.
SOGo may exist inside the engine as a by-product of mailcow, but it must not
become the permanent MateMail customer interface unless explicitly requested.

**Open sub-decision:** how webmail authenticates. MateMail deliberately does not
store mailbox passwords, so a webmail session cannot be established from
MateMail's own credentials alone. The likely mechanism is a Dovecot master user
so a verified MateMail session can be exchanged for an authenticated IMAP
session. This must be resolved before webmail is built, and recorded here.

**Interim position:** until MateMail webmail exists, customers use standard
IMAP/SMTP clients with MateMail-issued mailbox credentials, documented in
MateMail's own UI as `imap.matemail.online` / `smtp.matemail.online`.

**Launch impact:** none. Per DEC-012 webmail is explicitly *not* a launch
blocker, and may be built before or after the public launch.

---

## DEC-007r — DKIM: MateMail owns the capability, the Mail Engine owns the key

**Date:** 2026-09-10 (revised same day after review)
**Status:** Accepted — replaces DEC-007
**Relates to:** DEC-011

**The distinction this decision turns on:** owning a *product capability* is not
the same as holding the *secret material* behind it. DEC-011 gives MateMail the
former. It does not require the latter, and taking the latter is worse security.

### Decision

**MateMail owns the DKIM product capability:**

- customer-facing UI and DNS setup instructions
- publication verification and health scoring
- key status (present, selector, age)
- the rotation workflow customers and operators trigger
- the audit trail for every DKIM event

**The MateMail Mail Engine generates and securely stores the DKIM private key.**
The key is created inside the engine, never leaves it, and is used only for
signing there.

**Django does not normally store the DKIM private key.** MateMail retrieves only
the public key material needed for DNS display and verification, via
`MailEngineAdapter.get_dkim_public_key()`.

### Reasoning

- The signing key is the one secret whose compromise lets an attacker forge mail
  as any customer domain. It should exist in exactly one place, held by the
  component that actually needs it to sign.
- Copying it into the control plane widens the blast radius to the Django
  database, its backups, its admin, and every log or error path that might touch
  it — for no product benefit, since customers only ever need the public half.
- Every customer-facing capability listed above is achievable with the public key
  plus adapter operations. Rotation is an *instruction* to the engine, not a key
  transfer.
- This restores the security intent of the superseded DEC-007 while keeping the
  product ownership DEC-011 requires. The two are not in conflict.

### Technical debt: the current implementation is wrong and must be migrated

Today `apps/domains/views.py` generates a 2048-bit keypair in Django, stores the
PEM **unencrypted** in `Domain.dkim_private_key`, and pushes it to the engine
over **plaintext HTTP**.

An earlier draft of this record proposed keeping that behaviour on the grounds
that it already exists. **That reasoning is rejected.** An existing incorrect
implementation does not get to define the target architecture; it defines the
migration backlog.

Required migration, to be executed as part of the real Mail Engine integration:

1. Move keypair generation into the engine; MateMail requests creation and reads
   back only the public key.
2. Add `rotate_dkim_key()` to the adapter port so rotation never moves a private key.
3. Backfill existing domains: have the engine adopt or regenerate each key, then
   **purge `Domain.dkim_private_key`** and drop the column.
4. Until the column is gone, treat it as a live secret: encrypt at rest, keep it
   excluded from Django admin (done in P0), and never log or serialize it.
5. Secure the adapter transport regardless, so no secret crosses it in clear.

A regeneration changes the published DNS record, so the backfill must be
sequenced per domain with the customer's DNS update, not run as a bulk job.

### Consequences

- `Domain.dkim_private_key` is deprecated on sight. No new code may read or write it.
- The adapter gains `rotate_dkim_key()`; `get_dkim_public_key()` becomes the sole
  source of DKIM material in MateMail.
- Until migration completes, MateMail holds a high-value secret it should not
  hold. Tracked as an open security risk in `PROJECT_STATUS.md`.

---

---

## DEC-012 — Launch sequencing and the private beta gate

**Date:** 2026-09-11
**Status:** Accepted
**Decision maker:** NetaMate Solutions (product direction)
**Relates to:** DEC-005r (webmail), DEC-011 (integrated product)

**Context:**
An earlier draft roadmap proposed the earliest safe launch after P6 (backups),
treating P7 — the operational surface — as work that could follow live traffic.
That was wrong and is corrected here.

**Decision:**

1. **P0–P7 must all be complete before any real customer mail reaches the
   platform.** No exceptions, and "started" does not count.
2. After P7, MateMail may enter a **small controlled private beta** of roughly
   5–10 friendly tenants on real domains.
3. **P9 gates the broader public and commercial launch**, not the private beta.
4. **P8 (custom MateMail webmail) is not a launch blocker.** Customers initially
   use Outlook, Apple Mail, Thunderbird, phone mail apps and other standard
   IMAP/SMTP clients. P8 may land before or after P9 as product priority
   dictates.

Sequence:

```
P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → PRIVATE BETA → P9 → PUBLIC LAUNCH
                                          (P8 anywhere after P4)
```

**Reasoning:**

- P7 delivers monitoring, drift reconciliation between MateMail and the Mail
  Engine, mail queue visibility, storage sync and operational alerting. Without
  those, an operator cannot see what the platform is doing, cannot detect that
  MateMail's records and the engine disagree, and is not told when mail stops
  flowing or when outbound volume spikes.
- Holding someone else's business email is a custody responsibility. Accepting
  real mail without the ability to observe it is not a risk worth taking to save
  a week, and mail problems are frequently silent — an undetected delivery
  failure looks exactly like a quiet inbox.
- Splitting the launch in two lets the beta expose operational reality while the
  blast radius is 5–10 known tenants who can be contacted directly.
- Webmail is a large build and a convenience, not a prerequisite: business
  customers overwhelmingly already use a desktop or mobile mail client. Blocking
  launch on it would delay revenue by weeks for no gain in safety.

**Consequences:**

- The private-beta entry criteria are recorded in `PROJECT_STATUS.md` under
  *Private Beta gate*, and are checked as a gate rather than assumed.
- P6 is a beta prerequisite, not a post-launch task.
- Every private-beta incident feeds P9 as a test, runbook or product change.
- **Changing item 4** — making webmail block the public launch — requires an
  explicit successor decision recorded here. It must not be changed by
  amending the roadmap alone.

---

## Open Decisions (to be resolved in later phases)

| ID | Question | Target phase |
|----|----------|-------------|
| TBD-A | Stripe vs. placeholder for billing | Phase 11 |
| TBD-B | ~~Caddy vs. Nginx~~ — **resolved:** host-native nginx (NetaMate model) | closed |
| TBD-C | Mail storage volume strategy (local vs. S3) | Phase 14 |
| TBD-D | RBL/DNSBL configuration for spam | Phase 10 |
| TBD-E | ClamAV enable/disable in the Mail Engine | engine integration phase |
| TBD-G | Webmail auth mechanism (Dovecot master user vs. alternative) — see DEC-005r | before webmail is built |
| TBD-H | App-password model for IMAP/SMTP clients once web login uses 2FA | before general availability |
| TBD-F | MTA-STS hosting strategy (HTTPS required) | Phase 5 |


---

## DEC-013 — MateMail owns its platform transactional email

**Date:** 2026-09-11
**Status:** Accepted
**Decision maker:** NetaMate Solutions (product direction)
**Relates to:** DEC-001 (mailcow), DEC-011 (integrated product), DEC-012 (launch sequencing)
**Supersedes:** the P3c position that platform mail must go through a third-party SMTP provider

**Context:**
P3c built a vendor-neutral SMTP path for MateMail's own system mail — account
verification, password reset, team invitations, security notices — on the
reasoning that a platform whose password reset stops working when its mail
system is down is a platform nobody can recover an account on. That reasoning
led to an external provider (Postmark was evaluated) for those four message
types.

The product direction is that MateMail owns its mail infrastructure end to end.
Running the platform's own mail through a third party contradicts that, adds a
vendor dependency and a second reputation surface to manage, and means the
product does not use the thing it sells.

**Decision:**

1. **MateMail's Mail Engine will deliver platform transactional email**, not a
   third-party SMTP provider. No external provider account will be opened, and
   no external SMTP credentials will be configured.
2. **P4 must provide first-party infrastructure for both** customer business
   email (send and receive) **and** MateMail's own platform mail from
   `MateMail <noreply@mail.matemail.online>`.
3. **The real transactional delivery test moves to the first suitable P4
   milestone.** It is deferred, not waived, and P3 is not to be recorded as
   having passed it.
4. Until P4 replaces it, production keeps `MAIL_ENGINE_ADAPTER=stub` and
   `EMAIL_HOST` unconfigured. The application already refuses to send rather
   than reporting a false success, so this state is safe and visible.

**Consequences:**

- The application code needs **no change**. It is plain `django.core.mail` SMTP
  with no provider-specific coupling, so pointing it at the Mail Engine is a
  configuration change. P3c's `EMAIL_HOST` guard rejects loopback, which the
  Mail Engine must not be reached through — it will be a named host, not
  `localhost`.
- **The recovery-path risk in the original reasoning is accepted, not solved.**
  If the Mail Engine is down, password resets and verification emails stop with
  it. P4 owns mitigating this — at minimum, alerting that distinguishes
  "platform mail is failing" from "customer mail is failing", so the outage is
  known before customers report it. This is the real cost of the decision and
  is written down rather than glossed.
- The dedicated sending identity stays `mail.matemail.online`, separate from
  customer mail domains, so platform mail keeps its own reputation and
  authentication story.
- The P4 delivery test must verify actual inbox delivery plus SPF, DKIM, DMARC
  where applicable, PTR/HELO alignment, and no underlying engine branding
  leakage. SMTP acceptance alone does not count.
