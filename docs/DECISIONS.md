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

### Amendment (P4C-A) — the engine owning the key implies owning its deletion

Handing key custody to the engine was the right call, and it carries an
obligation that was not obvious until the engine was measured: **the engine does
not delete a domain's key when the domain is deleted.** Custody without a
deletion contract means a key with no owner, which the next registrant of that
domain name inherits.

So DEC-007r now reads: the engine owns the private key *for as long as MateMail
says the domain exists, and not one moment longer.*

Three additions:

- The port gains `delete_dkim_key(domain_name)` — idempotent, engine-neutral,
  and a **security operation rather than housekeeping**. Deprovisioning calls it
  unconditionally, including when the domain is already gone.
- `rotate_dkim_key` is `delete/dkim` → `add/dkim`, because the engine refuses to
  generate over an existing key. The previous single `add/dkim` could never have
  succeeded against a real engine.
- The selector MateMail publishes is **the one the engine reports**, never a
  default substituted when the engine says nothing. The engine is what signs; a
  guessed selector publishes a record for a key nobody signs with, and every
  message fails DKIM silently.

Detail in `MAIL_ENGINE.md` § "DKIM lifecycle"; the cross-tenant scenario and its
regression coverage in `SECURITY.md`.

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

> ## ✅ CLOSED — delivered and verified 2026-09-12 (P4C-B)
>
> The deferred transactional delivery test has been **performed and passed**.
> One message was sent from production through `send_transactional()` — the same
> function every verification link and password reset uses — and arrived in the
> recipient's **Gmail Primary Inbox**, not Spam.
>
> | | |
> |---|---|
> | SPF | **pass** — `smtp.mailfrom=noreply@mail.matemail.online`, `client-ip=169.58.114.252` |
> | DKIM | **pass** — `header.i=@mail.matemail.online`, `header.s=mm1` |
> | DMARC | **pass** — `header.from=mail.matemail.online` |
> | PTR / HELO / A | aligned — all three are `mx.matemail.online` ↔ `169.58.114.252` |
> | Transport | TLS 1.3 to `gmail-smtp-in.l.google.com`, certificate verified |
> | Gmail response | `250 2.0.0 OK` |
> | Placement | Primary Inbox |
> | Queue after send | empty; no defer, no bounce |
>
> Point 4 of the decision is superseded: production now runs
> `MAIL_ENGINE_ADAPTER=mailcow` with `EMAIL_HOST=mx.matemail.online`.
>
> **This proves the path works. It does not guarantee future deliverability or
> inbox placement.** One message to one provider from a new sending IP is
> evidence that authentication and routing are correct, not evidence of
> reputation. Volume, warm-up, and behaviour at other providers are unproven —
> and Microsoft's TCP/25 timeout remains unexplained.
>
> The recovery-path risk named below is still accepted and still unmitigated:
> if the engine is down, account recovery is down. That remains P7's alerting
> requirement.

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

---

## DEC-014 — MateMail reaches the Mail Engine over a dedicated private link, not a host-published port

**Date:** 2026-09-11
**Status:** Accepted
**Supersedes:** the P4A/P4C-A assumption that a Docker bridge gateway address is a private boundary

### Context

MateMail's backend and worker need the engine's API (8453) and SMTP submission
(587). The engine runs in its own Compose project on the same host, so the two
stacks have to meet somewhere.

The first implementation published both ports on MateMail's own app-network
gateway, `172.24.0.1`. It was never public, and it worked.

### The measurement that changed the decision

Containers on `netamate_internal`, `matedesk_internal`, `mateassist_internal`,
`mateconnect_internal`, `portfolio_default` and the default bridge **all opened
TCP connections to both ports.** Only `matemail_internal` could not, and only
because `internal: true` left it no route at all.

**A published bind address selects a destination address. It never restricts the
source.** `docker-proxy` holds a userland socket on that address and accepts on
it whichever bridge the traffic arrived from.

Rule counters showed the DNAT rule never fired during the test while the filter
ACCEPT did, once per connection — so the traffic does not traverse `DOCKER-USER`
and a rule there, the obvious remedy, would have done nothing. The same
re-origination made the engine observe one NAT address as the client for every
request, which is why its `allow_from` ACL could not identify the caller. One
root cause, two symptoms.

### Decision

Remove the socket rather than filter it.

- A **dedicated network**, `matemail_engine_link`, declared `external` and
  created `--internal`. Only `backend` and `celery-worker` join it.
- **One TCP passthrough gateway** owning the alias `mx.matemail.online`,
  forwarding 8453 to `nginx-mailcow` and 587 to `postfix-mailcow`.
- **No host port** on the application path.

### Reasoning

- **One ingress, because two would be non-deterministic.** Attaching both engine
  containers under one alias lets Docker's DNS return either address, while each
  port exists on only one of them.
- **Layer 4, because layer 7 would take custody of TLS.** Passthrough keeps the
  certificate MateMail validates the real one presented by the engine, so
  hostname verification stays meaningful end to end and the gateway holds no key
  material and parses no mail command.
- **Not nginx-mailcow itself**, despite its image having the stream module: the
  only writable, bind-mounted nginx path is included *inside* `http {}` where
  `stream {}` is invalid, and the one file with a top-level context is tracked
  in mailcow's git and restored by `update.sh`.
- **External network**, so neither stack's lifecycle can delete a network the
  other depends on. Verified by a deploy preflight, never created by one — a
  network made with Docker's defaults is routable, not internal, which would
  silently restore the problem.

### Consequences

- `extra_hosts` and both pinned application subnets are removed. They existed
  only to serve the rejected design; nothing outside the compose file now
  depends on which ranges Docker allocates.
- The engine's `allow_from` is scoped to the gateway's pinned address. It
  identifies the **gateway**, not the calling container, because the gateway
  originates the connection — that is the honest scope, and it is defence in
  depth behind reachability rather than a substitute for it.
- A new pinned image sits in the mail path and must be kept current like any
  other dependency.
- The engine's own loopback bindings remain for operator access by SSH tunnel.

---

## DEC-015 — MateMail Free: `@matemail.online` accounts, built after P7

**Date:** 2026-09-11
**Status:** Accepted — roadmap only. **Nothing is implemented.**

### Decision

MateMail will offer two mailbox models.

| | Address | Domain owned by | Status |
|---|---|---|---|
| **MateMail Business** | `rizwan@netamate.com` | the customer | built; the primary product |
| **MateMail Free** | `rizwan@matemail.online` | MateMail | roadmap, P7.5 |

Free accounts are conceptually what `@gmail.com` is to Gmail: a mailbox on a
domain the provider owns, claimed by username rather than by proving control of
a domain.

**The business model is not changing.** Custom-domain hosting remains the
primary enterprise product, and nothing about the current domain architecture is
altered to accommodate free accounts.

### Three identities, deliberately separate

```
user@matemail.online          free customer mailboxes          (P7.5)
user@customer-domain.com      business customer mailboxes      (built)
noreply@mail.matemail.online  platform transactional mail      (DEC-013)
```

`mail.matemail.online` stays the platform's own sending identity and must not be
collapsed into the free-user domain. They fail differently and they must fail
separately: a free user who gets a domain blocklisted must not be able to take
password resets and verification mail down with them, and reputation on the
subdomain that delivers account recovery is not something to share with public
signups.

### Placement: P7.5, after P7 and before Private Beta

```
P4     Mail Engine integration and activation
P5     mail policy, abuse controls, quotas, sending rules
       — INCLUDING the policy design for free accounts
P6     backup, restore, retention, safe deletion, disaster recovery
P7     operations: monitoring, alerting, queues, admin tooling
P7.5   MateMail Free — implement username@matemail.online
────────────────────────────────────────────────────────────────
PRIVATE BETA   both models: business custom domains AND free accounts
────────────────────────────────────────────────────────────────
P8     custom MateMail webmail
P9     public-launch readiness
PUBLIC LAUNCH
```

**Why not sooner.** Free public email carries a categorically higher abuse risk
than business hosting. A business mailbox requires someone to prove control of a
domain they paid for — an attacker who abuses it burns an asset with a cost and
a paper trail. A free mailbox costs a signup form, and the natural result is
automated signups, throwaway senders, and outbound spam that destroys the
sending reputation every *paying* customer depends on.

So the controls have to exist first, not be retrofitted:

- **P5** is where the policy framework is designed — sending limits, quotas,
  signup abuse controls, suspension. Without it there is nothing to enforce.
- **P7** is where an operator can actually *see* the platform — queue
  visibility, outbound volume, alerting. Without it, abuse is discovered by a
  blocklist rather than by us.

Opening free signup before both would be launching the highest-risk surface with
the fewest defences, on shared sending reputation.

**Why not later.** Private Beta should test both models. They exercise different
paths — signup and username claiming versus domain verification — and finding
out after launch that the free path was never exercised would be the wrong order
of discovery.

### P5 — design, do not implement

> **DISCHARGED by DEC-018 (2026-09-12)** — the P5 design deliverable this
> section calls for, including the classified reserved-username policy.

P5 designs the policy framework P7.5 needs. Topics to cover: free mailbox
storage quota; daily and hourly sending limits; recipient limits per message and
per period; anti-spam thresholds; signup abuse controls and bot protection;
account suspension; inactive-account policy; reserved usernames; username
lifecycle and re-use; recovery and verification rules; abuse reporting; rate
limits; and how all of it protects the shared sending reputation.

**No numeric limit is fixed here.** Picking a daily send cap before P5 has
analysed real behaviour would be inventing a number and then defending it.

### P7.5 — expected scope

Public username availability; reservation and normalisation rules; reserved and
system usernames; mailbox creation on `matemail.online`; free plan assignment
and quota enforcement; sending policy integration; signup verification; account
recovery; suspension and deactivation; inactive-account handling; admin
controls; abuse controls; mailbox lifecycle; and how a free account maps onto
MateMail's tenant/account ownership model.

### Reserved addresses are mandatory

P7.5 must ship a reserved-address system before any username can be claimed.
Illustrative, **not** a final list:

```
admin  administrator  postmaster  hostmaster  webmaster  root
abuse  security  support  billing  noreply  no-reply
```

`postmaster` and `abuse` are required by RFC 2142 and are where other operators
report problems; the rest are addresses a recipient would reasonably read as
speaking for MateMail. Letting a stranger claim any of them hands them the
platform's voice. P7.5 defines the complete policy, including how the list is
extended without breaking accounts already issued.

### Relationship to P8 (webmail)

P7.5 does **not** pull P8 forward. They are separate concerns, and the ordering
above stands unless a later product decision changes it.

The dependency worth naming: a free account is only worth launching broadly when
there is a practical supported way to *use* the mailbox. Business customers
already have IMAP/SMTP and their own clients; a free consumer audience largely
expects webmail. That makes P8 a gate on **broad** free availability, not on
building P7.5 or on including free accounts in a limited Private Beta.

### Consequences

- Free signup is not publicly available before Private Beta succeeds. The beta
  may cap the number of free accounts.
- The engine treats `matemail.online` as one more hosted domain, so no adapter
  or boundary change is anticipated — but `matemail.online` becomes a domain
  MateMail itself owns in the engine, which is a new ownership case for the
  tenant model to answer in P7.5.
- Abuse controls designed in P5 must be written with free accounts in view, not
  only business tenants, or they will need redesigning at P7.5.

---

## DEC-016 — Storage is three explicit plan limits; Private Beta is free, approved, 1 GB

**Date:** 2026-09-11
**Status:** Accepted. Implemented. **The beta policy is temporary and is not public-launch pricing.**

### The problem

Activating the real Mail Engine proved MateMail could not create a single
domain. The adapter sent a hard-coded domain total of `0` alongside a positive
per-mailbox ceiling, on the assumption that zero meant unlimited.

It does not. The engine reads three storage numbers and enforces all of them:

```
default per mailbox  <=  maximum per mailbox  <=  total for the domain
```

A ceiling above the total is a contradiction, and the engine refuses the whole
domain (`mailbox_quota_exceeds_domain_quota`). Every mailbox call afterwards
then failed with `access_denied`, because the domain did not exist — one root
cause, two symptoms.

The `Plan` model had only one of the three numbers (`max_storage_per_mailbox_mb`).
The other two did not exist anywhere, so the adapter was inventing them.

### Decision

**Storage is three explicit fields on `Plan`**, carried on `DomainSpec`, sent
verbatim by the adapter:

| Field | Meaning |
|---|---|
| `default_storage_per_mailbox_mb` | what a new mailbox starts with |
| `max_storage_per_mailbox_mb` | the largest a single mailbox may grow |
| `max_storage_total_mb` | the pool shared by every mailbox on the domain |

**The total is stored, never derived.** `total = mailboxes x ceiling` is one
plan shape, not the only one. Deriving it in the adapter would hard-code a
policy that every mailbox may simultaneously reach its maximum and would
foreclose a genuinely shared pool — 10 mailboxes, 5 GB each, 25 GB shared — for
no benefit. The adapter's job is to send what the plan says, not to decide
commercial limits.

**Validation is MateMail's, not the engine's.** `DomainSpec` checks the
relationship and raises a typed `InvalidStorageConfiguration` before any request
is built, so an impossible plan fails as our error in our words. Nothing is
silently clamped: lowering a ceiling to fit a pool would quietly sell less than
the plan promises, so the contradiction has to surface for an operator to fix.

### Private Beta policy — temporary

```
Price         : free
Provisioning  : admin approval required
Mailbox size  : 1 GB default and 1 GB maximum
```

Applied to the **`trial` plan** (the free tier), whose per-mailbox ceiling rises
from 512 MB to 1024 MB, with a pool of `max_mailboxes x 1 GB`.

The `starter`, `business` and `infrastructure` tiers were **deliberately not
flattened** to this policy. They have zero subscriptions today, but they are the
intended commercial ladder, and rewriting Business from 5 GB mailboxes to 1 GB
would be discarding a product decision this work has no business making. They
keep their ceilings, gain a 1 GB starting default, and get a pool that lets
every mailbox reach its ceiling.

Resulting values:

| tier | mailboxes | default | max/mailbox | total |
|---|---|---|---|---|
| trial *(beta)* | 5 | 1024 | 1024 | 5120 |
| starter | 10 | 1024 | 1024 | 10240 |
| business | 50 | 1024 | 5120 | 256000 |
| infrastructure | 500 | 1024 | 20480 | 10240000 |

Storage may be increased later. **None of this is final public pricing**, and
prices and feature flags were not touched.

### Admin approval — recorded here, built in P5

> **RESOLVED by DEC-017 (2026-09-12).** The paragraph below described the state
> at the time of this decision and is kept for the record. Approval is now
> enforced: signup creates a workspace in `PENDING_APPROVAL`, and mail access
> requires both a mail-enabled status and an `approved_at` timestamp set by a
> named platform admin.

The beta requires admin approval before a workspace is provisioned. **No such
mechanism exists in the codebase.** Signup at `apps/accounts/views.py`
(`SignupView`) creates a workspace immediately, and `TenantStatus` has no
pending-approval state — its values are trial / active / past_due / suspended /
cancelled.

This is recorded rather than built: adding an approval workflow is a real
feature, not a side effect of a quota fix. Until it exists the policy is
enforced operationally (nobody is invited), which is adequate while there are
zero tenants and is **not** adequate once signup is reachable. The enforcement
point is workspace creation, and it belongs with the P5 policy work.

### Not this decision

This says the current beta costs nothing and needs approval. It does **not**
implement DEC-015's free `@matemail.online` addresses — that remains P5 for
policy design and P7.5 for implementation. Business custom-domain hosting
remains the product architecture.

### Consequences

- `Plan` gains two fields; migrations `billing.0004` (schema) and
  `billing.0005` (data).
- The fake engine now enforces the same invariant, because a double that
  accepts what production refuses converts an outage into a green suite —
  which is exactly what let this ship.
- A plan whose numbers contradict each other cannot provision a domain, and
  says so on the domain record instead of failing silently.

---

## DEC-017 — Mail capability is a granted state, not an inferred one

**Date:** 2026-09-12
**Status:** Accepted. Implemented in P5.

### The problem

Before P5, "may this workspace send mail?" was answered independently, and
differently, by every piece of code that needed to know. Domain creation
compared `tenant.status`. The SMTP bridge compared it again, with a different
list of acceptable values. The Celery provisioning task did not compare it at
all. Suspension set a database column that nothing outside the bridge consulted.

Each of these was defensible on its own, and together they had a predictable
property: the endpoint that forgot a check is the one an attacker finds. The
specific holes measured during P5:

| Path | Before |
|---|---|
| `provision_domain_task` | no capability check at all |
| `AdminTenantSuspendView` | set a column; the engine kept the domains active |
| `AdminTenantActivateView` | could move a never-approved workspace to ACTIVE, where mail then silently failed |
| Outbound bridge | tenant status compared inline, with its own notion of "active" |
| Rate limits | three literals, identical for every plan, invisible to the product |
| Platform sender | would have been rejected as "Sender mailbox not found" |

### Decision

**One authoritative answer, in `apps/tenants/policy.py`, that every path asks.**

```
assert_can_use_mail(tenant)   # may it create or change engine resources?
assert_can_send_mail(tenant)  # may it send, right now?
```

Capability is a **granted state with a recorded grantor**, not a property
inferred from a status string. A workspace can use the Mail Engine only when
both hold:

1. its status is in `MAIL_ENABLED_STATUSES` (trial or active), and
2. `approved_at` is set — a platform admin explicitly allowed it.

The second is what makes this a gate rather than a convention. A status can be
set by a fixture, a shell, a future admin action that forgets; the approval
timestamp is a fact with a name and a time attached to it, and mail access
requires that fact.

Signup therefore creates a workspace in `PENDING_APPROVAL`. Anyone may sign up;
signing up grants nothing.

### Everything here fails closed

An unknown status, a missing subscription, a tenant that cannot be loaded, a
rate limiter that cannot be reached: all denied. `MAIL_ENABLED_STATUSES` is an
allowlist, so a status added later is denied until someone decides otherwise,
and the denial is logged loudly rather than passing quietly.

### Suspension is two mechanisms, not one column

A suspended workspace whose domains are still active in the Mail Engine is
stopped only by the policy bridge — one service, one configuration line and one
restart away from not running. An abuse suspension that quietly does nothing is
worse than none, because an operator believes the problem is handled.

Suspension now also deactivates the workspace's domains in the engine
(`apply_tenant_suspension_task`). The customer stops sending if **either**
mechanism works. Whether the engine-side half was queued is reported back to the
operator rather than logged and forgotten.

Deactivate, never delete: suspension is reversible by design, and deleting a
domain to enforce a temporary state would destroy stored mail.

### Three graded abuse responses

Reach for the narrowest one that stops the damage.

| Response | Scope | Reversible | Customer keeps |
|---|---|---|---|
| `set_mailbox_suspended` | one mailbox | yes | everything else in the workspace |
| `set_outbound_enabled(False)` | one workspace's sending | yes | inbound mail, settings, administration |
| `suspend_tenant` | one workspace entirely | yes | its data |

Mailbox `SUSPENDED` is deliberately a state only MateMail can set or clear. A
tenant admin's own control offers active and disabled only, and refuses to touch
a suspended mailbox at all — otherwise a customer could re-enable the account
MateMail just stopped. That asymmetry is the entire reason for having two states
that both mean "not sending".

Every transition requires an authenticated actor, takes a row lock, and writes
an audit event. Letting a workspace send affects the reputation every other
customer shares, so "who allowed this, and when" has to be answerable months
later.

### Sending limits come from the plan

Volume is a commercial dimension, so it belongs on `Plan`, not in three literals
inside the limiter:

| Tier | Aliases | Messages/hour/mailbox | Messages/day/workspace |
|---|---|---|---|
| trial (Private Beta) | 50 | 50 | 500 |
| starter | 100 | 100 | 1,000 |
| business | 500 | 200 | 5,000 |
| infrastructure | 2,000 | 300 | 20,000 |

Conservative on purpose. A workspace that needs more can ask and be given more
in seconds; a workspace that discovers it can already send thousands costs every
other customer their deliverability. A workspace with **no** resolvable plan gets
a tighter fallback still — absence of a plan is a configuration gap, and the safe
reading of a gap is the tighter one.

The check and the increment happen inside one Redis script. The previous
implementation read every counter, decided, then incremented in a separate
round-trip while its docstring claimed atomicity; under the concurrency that
actually matters — a compromised account sending as fast as it can — the limit
leaked reliably.

Aliases are capped because an alias is a free forwarding address, which makes it
the cheapest way to turn one approved workspace into many sending identities.

### Refusals are classified: delayed, not destroyed

A remote server told 5xx returns the message to its sender immediately; told 4xx
it holds and retries for days. Every refusal in the bridge used to be a flat
REJECT, so a workspace suspended over a billing question on a Friday permanently
bounced every message sent to it until Monday — and those messages were gone,
with the senders told the addresses did not work.

The rule: **refuse permanently only when the answer will not change.**

| Condition | Inbound | Why |
|---|---|---|
| domain not hosted here | REJECT | settled |
| mailbox does not exist | REJECT | settled — and deferring would make MateMail a backscatter source |
| workspace cancelled or rejected | REJECT | a decision already made |
| workspace suspended / past due / pending | DEFER | expected to resolve |
| mailbox suspended or disabled | DEFER | a pause, not a statement that the address never existed |
| domain inactive | DEFER | nearly always temporary, and still a domain we host |
| MateMail cannot tell | DEFER | "I do not know" must never be expressed as "this address does not exist" |

**Outbound is deliberately asymmetric and rejects even reasons inbound defers.**
Inbound mail belongs to a third party who should not lose it over our customer's
billing problem. Outbound mail belongs to the customer, who is sitting in front
of a mail client and is better served by an immediate, clear refusal. It also
matters for abuse: deferring a suspended workspace's outbound would accumulate a
spool of exactly the mail we suspended them for, and release it when the
suspension lifted. The one outbound exception is a rate limit, which defers
because the sender is within policy and merely early.

### The platform sender is allowed, not exempted

MateMail's own service identity (`noreply@mail.matemail.online`, DEC-013) sends
verification emails, password resets and invitations through the same submission
path as a customer. It has no mailbox, no domain and no tenant, so every check in
the bridge would have rejected it — and account recovery for every customer would
have stopped the moment the policy service was enforced in the engine's
restrictions.

It is now recognised explicitly (`PLATFORM_SENDER_ADDRESSES`), and that allowance
is deliberately narrow:

- it is checked **after** sender == authenticated username, so possession of the
  platform credential lets you send as the platform and as nothing else;
- it has its own finite hourly ceiling — trusted to send is not the same as
  trusted to send without bound, and the realistic failures here are a leaked
  credential and a retry loop, both of which a ceiling contains;
- tenant policy is skipped because there is no tenant to have a policy, which
  also means an abuse response against one customer cannot silence MateMail's
  own password resets.

### Ownership is re-checked, and only flagged

Ownership was proved once at onboarding and never looked at again. When a domain
changes hands — a lapsed registration, an acquisition, an ended contract — the
former tenant keeps the VERIFIED row and keeps receiving the mail, while the new
owner's claim can never succeed, because the partial unique index gives that row
to exactly one tenant. The correct owner has no self-service path at all.

A daily sweep re-checks every verified domain and counts consecutive failures.
It deliberately **does not** deprovision anything: the signal is the absence of a
DNS record, and DNS is absent for many reasons that are not a change of
ownership. Cutting off a legitimate customer because of a bad week of lookups
would do more damage, more often, than the case being defended against. Seven
consecutive failures raise a flag once, and a human decides.

### Message-ID

Django generates a Message-ID from the local hostname when none is supplied. In
a container that is the container id, so real delivered mail carried
`<...@00a3f41e29d7>` — a public header advertising an internal identifier,
different after every deploy, and not a resolvable domain. It is now a `uuid4`
rooted at the sending domain.

### Consequences

- `Tenant` gains `status` choices `pending_approval` / `rejected`, plus
  `approved_at`, `approved_by`, `review_reason`, `outbound_disabled`,
  `outbound_disabled_at`. Migrations `tenants.0002` (schema) and `tenants.0003`
  (backfill).
- `Plan` gains `max_aliases`, `max_messages_per_hour_per_mailbox`,
  `max_messages_per_day_per_tenant`. Migrations `billing.0006` / `billing.0007`.
- `Domain` gains `ownership_recheck_failures`. Migration `domains.0006`.
- Existing workspaces that were already trial or active are recorded as approved
  by the backfill, with no approver and an explicit reason — the schema change
  must not revoke access retroactively as a side effect.
- The engine-side integration is versioned in `deploy/engine/postfix-extra.cf`
  (the restriction override), `deploy/engine/docker-compose.override.yml` (the
  bridge sidecar) and `scripts/postfix_policy_bridge.py`. Installed by
  `scripts/install-policy-bridge.sh`. **Built and tested, not yet deployed.**

### How Postfix is made to ask

MateMail deciding correctly is worth nothing if the engine never asks, and the
engine did not. Three things were wrong, and each would have been silent.

**The hook was advised in the wrong place.** Appending `check_policy_service` to
`smtpd_recipient_restrictions` puts it after `permit_sasl_authenticated`, where
Postfix stops evaluating the moment a client authenticates — so every
authenticated submission, exactly the traffic MateMail needs to rule on, would
short-circuit before reaching it. The service would be installed, running, and
never asked. It now sits *before* that permit, and *after* `permit_mynetworks`
so the engine's own unauthenticated injections (watchdog probes, quarantine
digests) are settled before customer policy is consulted.

**Counting at the wrong stage.** A recipient-stage hook fires once per RCPT TO,
so a message to five people would cost a sender five messages. A second hook at
`smtpd_end_of_data_restrictions` — which upstream leaves empty — fires once per
message, and that is where the counter moves. The stage is explicit in the
request: `stage=rcpt` authorizes without counting, `stage=end_of_data`
authorizes and records. A caller that omits it gets the full check, so a stage
cannot be forgotten into a free send.

**The wrong topology.** The earlier design ran the bridge on the host and opened
a firewall hole for the Docker subnet — the same published-socket arrangement
measured and rejected in DEC-014, on a socket that decides whether mail may be
sent. It is now a sidecar in the engine's own Compose project, on the engine
network and the existing private link, with no host port. Its systemd unit was
deleted rather than left in the repository beside the replacement.

### The bridge answers DUNNO, never OK

In Postfix a policy service's `OK` means "permit and stop evaluating this list".
The entry after ours is `reject_unauth_destination` — the anti-relay check — so
`OK` would not merely permit a message, it would skip the check that stops
MateMail relaying for domains it does not host. `DUNNO` leaves every later
restriction in force. There is a test asserting no code path can emit `OK`.

A MateMail outage answers `DEFER_IF_PERMIT`: mail is retried rather than passed
unchecked or bounced permanently, and a relay attempt a later restriction would
reject outright is still rejected.

### Nothing upstream was replaced

The engine's own defences all remain, and several overlap MateMail's
deliberately: `reject_authenticated_sender_login_mismatch` rejects a spoofed
MAIL FROM earlier than any policy service can be consulted, and
`smtpd_relay_restrictions` makes unauthenticated relay impossible independently
of what the policy service answers — including while it is down. The earlier
advice to set `smtpd_sender_restrictions = check_policy_service …` would have
discarded all of that for a narrower duplicate.

### Aliases are authorized senders

`sender == sasl_username` alone was too strict: it refused mail the engine had
already accepted. The engine's sender ACL treats an alias whose `goto` is the
logged-in mailbox as a permitted sender, so MateMail now does too — matched on
the `destination_mailbox` foreign key, which cannot reach across tenants.

Rate limits are charged to the authenticated mailbox rather than the envelope
sender, so an account cannot spread its quota across the aliases it holds.

---

## DEC-018 — MateMail Free: the policy framework (design only)

**Date:** 2026-09-12
**Status:** Accepted as design. **Nothing is implemented. Implementation is P7.5.**

DEC-015 placed free `@matemail.online` accounts at P7.5 and assigned the policy
design to P5. This is that design. It fixes no numbers that require data
MateMail does not yet have, and it builds nothing: there is no free signup, no
username claiming, and no free mailbox provisioning in P5.

### The one structural difference

A business mailbox requires someone to prove control of a domain they paid for.
An attacker who abuses it burns an asset with a cost and a paper trail. A free
mailbox costs a signup form. Every policy below follows from that difference,
and none of it is a tightening of the business policy — it is a different risk
class.

### What P5 already built that P7.5 inherits

The framework is deliberately not free-specific, so P7.5 configures rather than
builds:

| Need | Mechanism | Status |
|---|---|---|
| no sending before a human allows it | `approved_at` + `assert_can_use_mail` | built |
| per-plan sending volume | `Plan.max_messages_per_*` | built |
| stop one account | `set_mailbox_suspended` | built |
| stop one account's sending only | `outbound_disabled` | built |
| atomic, fail-closed counting | Redis script in `rate_limits.py` | built |
| audit with an actor | `LogEventType.*` | built |

A free account is a tenant with one mailbox on a MateMail-owned domain and a
`free` plan. The plan carries the limits. **No new enforcement path is
anticipated** — which is the point of having designed the framework first.

### Policy positions taken now

**Approval.** Free accounts cannot use the beta's human-approval gate — it does
not scale past the first few hundred signups. P7.5 must replace it for free
accounts with verified email plus a signup abuse control, and an approved-state
grant that is still explicit and still recorded. The gate stays; what satisfies
it changes.

**Sending.** A free account's limits must be strictly below the lowest paid
tier's, and a new free account's lower still until it has any history. Numbers
are P7.5's, to be set against observed beta behaviour rather than invented here.

**Forwarding to external destinations is the highest-risk feature on a free
account** and must not ship enabled. It converts a free mailbox into an
untraceable relay with our reputation attached, and it is the one capability
that makes automated free signups worth the attacker's trouble.

**Inactivity.** A free mailbox that is never read still receives mail and still
consumes storage, and a dormant account with a guessable password is an asset
someone else will eventually use. P7.5 defines dormancy, notice, and what
happens to the address afterwards — but a released username must never
silently deliver its predecessor's mail to its successor.

**Suspension.** Identical mechanisms, shorter fuse. The graded responses in
DEC-017 apply unchanged.

### Reserved usernames

P7.5 must ship reservation **before** the first username is claimed; a list
applied afterwards cannot take back an address already issued. Three classes,
because they have different justifications and different rules for change:

**1. Protocol-required.** RFC 2142 and RFC 2822 §4: other operators and
automated systems expect these to reach a human at the domain owner.

```
postmaster  abuse
```

These must resolve to MateMail and are never claimable. `postmaster` and `abuse`
in particular are where blocklist operators and other providers report problems
— losing them means losing the ability to be told we have a problem.

Also reserved on the same footing, from the wider RFC 2142 set, as MateMail owns
the domain: `hostmaster`, `webmaster`, `security`, `noc`.

**2. Operationally required.** Not mandated by any RFC, but addresses MateMail
itself uses or will use for automated mail; letting them be claimed creates a
collision with our own systems.

```
noreply  no-reply  donotreply  do-not-reply  bounce  bounces
mailer-daemon  daemon  system  notifications  alerts  automated
```

**3. Brand-protected.** Addresses a recipient would reasonably read as speaking
for MateMail or NetaMate. Letting a stranger claim any of them hands them the
platform's voice — a phishing mail from `billing@matemail.online` needs no
spoofing at all, because it would be genuinely sent from our infrastructure,
with our DKIM signature.

```
admin  administrator  root  sysadmin  matemail  netamate
support  help  helpdesk  contact  info  sales  billing
accounts  account  payments  invoice  legal  privacy  careers
team  staff  office  news  press  marketing  service  verify
verification  password  reset  login  signin  auth  secure
```

**Normalisation must happen before the list is consulted**, or the list is
decorative. `Adm1n`, `ad-min`, `аdmin` (Cyrillic а) and `admin.` are all attempts
at the same address. P7.5 defines casefolding, separator handling, confusable
scripts and dot-insensitivity, and applies them *before* matching. A reservation
system that matches only the exact ASCII string is a reservation system that does
not work.

**Extending the list later** must not break accounts already issued. The rule:
adding a name blocks new claims immediately and never revokes an existing
account automatically — an existing holder of a newly-reserved name is a
decision for a human, with notice and a migration path.

### Not decided here

Free storage quota; specific send caps; recipients per message; anti-spam
thresholds; which bot protection; verification and recovery mechanics; whether
free accounts are capped in number during the beta. All P7.5, all to be set
against real data. Picking them now would be inventing numbers and then
defending them.

### Consequences

- P5 ships **no** free-account code. The reserved list above is policy, not a
  module, precisely so that P7.5 implements it once with normalisation attached
  rather than inheriting a half-built constant.
- DEC-015's P5 obligation is discharged by this entry.

---

## DEC-019 — MateMail Native Mail Engine architecture

**Date:** 2026-09-13
**Status:** Accepted as architecture (NE0). **Nothing is implemented.** mailcow
remains the live production engine until NE8 explicitly retires it.

### Context

P4 chose mailcow (DEC-001) and built the `MailEngineAdapter` port around it.
P5 proved the port held: every mailcow reference in `backend/` outside
`mailcow_adapter.py`, `factory.py` and `checks.py` is a comment. The product
decision is now that the **Private Beta runs on a MateMail-native engine**, so
the orchestration layer mailcow provides has to be rebuilt on Postfix, Dovecot
and Rspamd directly.

Full design: `docs/NATIVE_MAIL_ENGINE.md` § NE0.

### The decisions

**Engine state lives in an engine-owned PostgreSQL**, written only by the
adapter, with MateMail remaining authoritative.

The alternative — having Postfix and Dovecot query MateMail's own database —
was rejected on two independent grounds. It would put every mail lookup on the
application database, so a routine Django migration holding a lock would stop
inbound mail for existing mailboxes; P5 deliberately accepted MateMail's
availability in the mail path for *policy decisions* and made that fail closed,
which is a far narrower coupling. And it would hand the Internet-facing
processes credentials to the database holding users, sessions, API keys and
billing. Generated flat files were rejected because provisioning would become
generate-and-reload, with atomicity and staleness to solve and a Postfix reload
per mailbox.

**Mailbox authentication is bcrypt (BLF-CRYPT) in the engine database**, matching
what mailcow stores today so the platform sender migrates without a password
reset. Mailbox passwords stay separate from MateMail account passwords; the
control plane stores neither the password nor the hash.

**Storage is Maildir++ at `/var/vmail/<domain>/<local_part>/`, uid:gid 5000:5000**,
matching mailcow's layout so the platform sender's maildir can be copied
verbatim. Quota is enforced by maildirsize rather than a database dict, so
enforcement has no database dependency.

**DKIM keeps DEC-007r exactly**: keys are generated inside the engine, stored
0600 on the Rspamd volume, and only public material crosses the port. Domain
deletion deletes the key first — the cross-tenant inheritance defect measured in
P4B must not return.

**Aliases separate three concepts mailcow conflates**: delivery alias, send-as
grant, and forwarding. `ensure_alias` writes both the delivery alias and the
send-as grant. This is the direct fix for the defect P5's production activation
found, where MateMail authorised alias sending but the engine rejected it at
MAIL FROM because the alias carried `sender_allowed=0`.

**The P5 policy integration is carried over unchanged** — the hook before
`permit_sasl_authenticated`, the end-of-data hook for once-per-message counting,
`DUNNO` and never `OK`, and fail-closed deferral. It is proven in production as
of 2026-09-13; there is no reason to redesign it. The policy bridge becomes a
first-class service rather than a sidecar in another project's Compose file.

**Rspamd gets a dedicated Redis**, not MateMail's application Redis, because the
application Redis is a Celery broker whose flush is routine and would destroy
accumulated spam training.

**Seven services**, all digest-pinned, no public ports through NE4, with only the
provisioning API and the policy bridge joining `matemail_engine_link`. Postfix,
Dovecot, Rspamd, the database and Redis have no route to MateMail at all.

### Malware scanning, DNS and the write path (NE0 addendum, 2026-09-13)

**ClamAV and Olefy are both retained.** Read-only inventory found `clamd-mailcow`
and `olefy-mailcow` running, wired into Rspamd at `clamd:3310` and `olefy:10055`,
with current signatures. MateMail sells business email; shipping a native engine
without the malware scanning the platform already has would be a security
regression customers could neither see nor consent to. Olefy in particular scans
OLE/macro content, the dominant malware vector in this market. A scanner that is
unreachable or slow causes a **deferral, never acceptance of unscanned mail**.
`freshclam` needs outbound HTTPS and is the only native service granted general
egress; signature age belongs in P7 monitoring.

**A dedicated validating resolver (Unbound) is required, not optional.** Postfix
runs `smtp_tls_security_level = dane`, and DANE derives its security entirely
from DNSSEC validation — verified in production, where `dnssec-failed.org`
correctly returns SERVFAIL through the engine's Unbound. Pointing the native
engine at the host resolver or a public one would leave outbound mail flowing
while the TLSA guarantee silently disappeared. The resolver is engine-network
only, never publicly exposed, and a resolver failure defers mail rather than
falling back to unvalidated answers.

These two decisions take the topology from seven services to **ten**.

**The engine database write path is stated precisely:**

```
MateMail                   is authoritative for all product state
NativeMailEngineAdapter    is the only EXTERNAL provisioning client
matemail-native-api        is the only DIRECT writer to the engine database
```

Postfix and Dovecot hold read-only credentials; Django never connects to the
engine database at all. An earlier draft said the engine DB was "written only by
the adapter", which was imprecise — the adapter does not touch it directly.

**NE6 moves three artifacts engine-to-engine on the host**, never through Django,
never through `MailEngineAdapter`, never through a workstation: the DKIM private
key (root staging, 0600 preserved, verified against the *public* DNS record and
the staging copy shredded), the bcrypt mailbox hash (SQL to SQL as an opaque
value, never in MateMail's database, verified by authenticating rather than by
inspection), and the maildir (`rsync -aHAX --numeric-ids`). DEC-007r and the
"MateMail stores no mailbox credential" rule both hold unchanged. mailcow keeps
a complete working copy of all three until NE8, which is what makes the cutover
reversible.

### What is not changing

**The `MailEngineAdapter` contract.** All 26 methods map to native mechanisms
with no change to the interface, the DTOs or the typed errors, so
`tests/test_adapter_contract.py` applies to the native adapter unmodified. That
is what keeps NE5 an adapter swap rather than a rewrite, and it is the whole
return on the P4A boundary work.

### Consequences

- MateMail takes on the upgrade burden mailcow carries today for Postfix,
  Dovecot and Rspamd. This is a real, accepted cost; NE4's validation suite is
  what makes it manageable, which is why NE4 is its own phase.
- Two data stores exist, so they can disagree. Mitigated by a single writer,
  idempotent `ensure_*` upserts, and P7's reconciliation pass.
- Sequencing: NE1–NE5, then P6 and P7, then NE6–NE7, then P7.5 and the Private
  Beta. P6 waits so backup tooling is written once against the store customers
  will actually use; NE6 waits because moving the platform sender moves the
  sending reputation, and that should not happen before backups and monitoring
  exist.
- mailcow removal (NE8) remains separately authorised and is never a side effect
  of another phase.

---

## DEC-020 — Postfix and Dovecot read the engine through views, not tables

**Status:** accepted (NE3, 2026-09-18)

NE2 granted the two reader roles `SELECT` on the base tables. NE3 replaces that
with six purpose-built views (migration 003) and revokes the table grants.

**Why.** Three things follow from it that table grants cannot give:

* *A stable contract.* Postfix and Dovecot depend on six shapes, not on the
  schema. A column rename would otherwise break a map file at delivery time
  rather than at deploy time.
* *Column-level least privilege.* `password_hash` is published to exactly one
  view, granted to exactly one role. Postfix cannot read a hash at all, which is
  not expressible with a table grant.
* *One definition of "active".* "A mailbox is usable when the mailbox is active
  **and** its domain is active" lives in the views. Copied into every map file
  and every query instead, one copy eventually disagrees, and a suspended tenant
  keeps sending.

**Consequence.** NE1's `ALTER DEFAULT PRIVILEGES ... GRANT SELECT ON TABLES` is
retired. It granted both readers access to every new relation automatically —
including views — and had already required a compensating `REVOKE` in migration
002. New relations are now readable by nobody until a migration grants them.

---

## DEC-021 — The DKIM selector is per domain, published by the engine API

**Status:** accepted (NE3, 2026-09-18) — refines DEC-007r

Rspamd resolves each domain's signing selector from
`/var/lib/rspamd/dkim/selectors.map`, written by the engine API from the
`dkim_key` rows. There is no global fallback selector.

**Why.** A single hardcoded selector is correct only for domains that have never
rotated. For any other, Rspamd looks for a key file that does not exist and
either sends unsigned or signs with a key whose name the published DNS record
does not match. A signature that fails to verify is worse than no signature: it
reads as a forgery rather than as unsigned mail.

**Why the API writes it.** The map is part of the key store, derived from the
same rows as the keys and required to change in step with them. The API is
already the only component permitted to write that store (DEC-007r), and Rspamd
mounts it read-only. Publication happens inside the DKIM lifecycle lock, so the
map can never reflect a half-completed rotation.

**Why a failure propagates.** If the map cannot be published the operation
fails, even though the key and row are already committed. A domain missing from
the map sends unsigned mail; reporting success for that is the quiet
half-failure the engine exists to prevent. Every operation is idempotent and
startup reconciliation republishes the map.

---

## DEC-022 — Dovecot reports logins to the API instead of writing them

**Status:** accepted (NE3, 2026-09-18)

`last_login_at` is updated by the engine API, called from Dovecot's auth-policy
hook, using a credential scoped to that single endpoint.

**Why not Dovecot's own `last_login` plugin.** It writes to a dictionary, which
would mean giving Dovecot write access to the engine database and ending the
single-writer invariant (NE0.2) for a cosmetic timestamp.

**Why a separate secret.** Dovecot is the most network-exposed component in the
engine. Reusing the provisioning secret so it could record a login would let a
compromised Dovecot create domains, mint mailboxes and rotate DKIM keys.

**Why it fails open.** `auth_policy_reject_on_fail = no`, and the hook reports
after authentication rather than gating it. Bookkeeping must not be able to lock
customers out of their mail. Only successful logins are recorded, so an
unauthenticated attacker cannot move a timestamp by guessing passwords.

---

## DEC-023 — A DKIM selector change retires the old key rather than deleting it

**Status:** accepted (NE3, 2026-09-18) — refines DEC-021

Rotating `mm1 -> alt7` keeps `<domain>.mm1.key` on disk, marked retired by a
sidecar file, for a grace period of 1800 seconds. Reconciliation removes it
afterwards.

**Why.** Rspamd resolves the selector from a file map it re-reads on its own
schedule. Deleting the old key the instant the map changed left a window where a
worker still holding the previous map opened a key that no longer existed and
sent the message UNSIGNED. The observed propagation was about 13 seconds, which
is a probability rather than an invariant — and an invariant is what signing
needs.

Retaining the key makes both concurrent outcomes correct signatures: a stale
worker verifies against the old DNS record, a fresh one against the new. Neither
is unsigned, and neither is signed with a key the published record does not name.

**Why not just shorten the refresh interval.** It reduces the probability and
establishes nothing. The design has to tolerate the documented upper bound and
process scheduling delays, not race them.

**Why a sidecar file and not a database column.** Crash consistency. The marker
is written BEFORE the new key is activated, so any crash leaves the retirement
recorded beside the key it describes. A row lost in a crash window would let
reconciliation classify a key that is still in use as an orphan and delete it
early — exactly the failure the mechanism exists to prevent.

**Why 1800 seconds.** A local file map refreshes at `map_watch_interval *
map_file_watch_multiplier` — 300 * 0.1 = 30s at the shipped defaults, measured
rather than assumed. 1800s is sixty times that, and short enough that retired
private key material does not accumulate.

**Why reconciliation owns cleanup.** It already runs at startup and through
`/v1/dkim/reconcile`, already holds the DKIM lifecycle advisory lock, and already
distinguishes live keys from orphans. Adding a scheduler for a deletion that is
allowed to be late would be a moving part with no benefit.

**Same-selector rotation is excluded deliberately.** The filename never changes,
so no worker can be looking for a name that vanished, and activation is an atomic
rename. Its real exposure is DNS caching of the old public key, which filesystem
consistency cannot address; that is a production rollover concern owned by NE7.

---

## DEC-024 — Mailbox storage is addressed by an immutable identity

**Status:** accepted (NE4, 2026-09-19)

Maildir lives at `/var/vmail/<domain>/<storage_id>`, where `storage_id` is
minted once per mailbox ROW and never reused. Deleting a mailbox removes its
provisioning state and KEEPS its mail, recorded in `retired_mailbox_storage`.

**Why.** NE3 runtime validation found that deleting a mailbox removed its rows
and left the Maildir on disk at a path derived from the email address.
Recreating that address pointed the new owner at the previous owner's mail. No
attacker is required — only staff recreating a mailbox somebody asked to have
deleted.

Making the path depend on a per-row identity means a recreated address is
*structurally* unable to reach the old directory. The alternative — deleting the
mail on `delete_mailbox` — would make a provisioning call destroy customer data
as a side effect, which is worse and is not what the contract asks for.

**Why existing rows keep their current path.** Migration 004 backfills
`storage_id` with the row's current `local_part`, so no live mailbox's storage
moves and there is nothing to migrate on disk — SQL cannot move files, and a
migration that renamed every path would orphan exactly the data it protects. The
fix still holds for those rows: the vulnerability is delete-then-recreate, and a
recreated mailbox is always a new row, which always gets a UUID.

**Who owns the retained data.** P6. `retired_mailbox_storage` exists so that
work has a list to act on rather than a directory tree to interpret.

---

## DEC-025 — Queue control runs inside the Postfix container

**Status:** accepted (NE4, 2026-09-19)

Queue and quarantine operations are served by a small control daemon that runs
beside Postfix in its own container, reached only by the Native API over a
shared-secret HTTP API on the engine network.

**Why not the alternatives.** Giving the Native API the Docker socket hands it
root on the host. Giving it a writable mount of the spool creates a second
writer on a queue directory and turns any path-traversal bug into spool writes.
Letting Django run `docker exec` couples MateMail to container names. The
control daemon is the smallest privileged surface: it lives in the one place
that already legitimately owns the spool, and it adds no service to the topology.

**What keeps it safe.** It runs no shell. Every Postfix invocation is a fixed
argv list whose only caller-supplied element is a queue id already matched
against `^[A-Za-z0-9]{6,32}$`. Release is `postsuper -H`, which cannot alter a
sender, a recipient or the content — so releasing is structurally incapable of
redirecting someone's mail.

---

## DEC-026 — Quarantine is the Postfix hold queue

**Status:** accepted (NE4, 2026-09-19)

Rspamd marks a suspicious message with `X-MateMail-Quarantine`, and a Postfix
`header_checks` rule moves it to the HOLD queue. `get_quarantine_items` reads
that queue; `release_quarantine_item` un-holds one message.

**Why.** The hold queue is the only Postfix state that already means "accepted,
retained, and going nowhere until a human says so". It is distinct from
`deferred` (will retry by itself) and `active` (being delivered now), so the
three operational situations stay distinguishable — which a single "not
delivered" list would destroy.

**Why the marker is not an Internet-reachable backdoor.** Rspamd removes any
inbound copy of the header before adding its own, so a remote sender cannot set
it. Even if they could, the only thing it achieves is quarantining their own
message: it cannot release one, cannot bypass scanning and cannot redirect mail.
The dangerous direction — escaping quarantine — is not expressible through it.

Confirmed malware is REJECTED outright rather than quarantined. Quarantine is
for suspicion; parking malware in a queue for someone to release by accident is
worse than refusing it.

---

## DEC-027 — A rate-limited submission is deferred, not bounced

**Status:** accepted (NE4, 2026-09-19)

Exceeding a per-mailbox submission limit returns `4.7.1` through
`DEFER_IF_PERMIT`. The identity charged is the AUTHENTICATED login, never the
envelope sender.

**Why 4xx.** A rate limit is a statement about timing, not about the message. A
permanent rejection would destroy mail that was never wrong and generate a
bounce for what is a throttling decision. A well-behaved client retries.

**Why the authenticated identity.** Charging the envelope sender would let a
mailbox spread its traffic across every alias it is entitled to send as, which
is precisely the budget the limit exists to cap.

**Why the limiter fails open on its own outage.** If the limit lookup or the
Redis counter is unavailable the message is allowed. This service caps volume;
it does not authorise mail. Authentication, sender ownership and relay control
have all already run and are unaffected. Refusing every submission because a
counter was unreachable would turn a metering outage into a total outage. The
Postfix side still DEFERS if the policy service itself cannot be reached at all.

---

## DEC-028 — Deployment hashes bind-mounted configuration

**Status:** accepted (NE4, 2026-09-19)

`deploy.sh` hashes each service's configuration directory and passes the result
as an environment variable, so Compose recreates a container exactly when its
configuration changed.

**Why.** Compose compares images, environment, mounts and labels — never the
CONTENT of a bind-mounted file. NE3 updated `rspamd/local.d`, `docker compose up
-d` reported success, and Rspamd ran for five days with the previous rules: the
antivirus scores meant to reject malware were never loaded. It was found by
checking, not by anything failing.

Documenting "remember to restart Rspamd" would not have prevented it. Hashing
makes the change visible to the tool that decides what to recreate.
