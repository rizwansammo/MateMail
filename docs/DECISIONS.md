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

### Admin approval — policy recorded, enforcement missing

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
