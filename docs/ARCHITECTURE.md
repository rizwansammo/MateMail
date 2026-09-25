# ARCHITECTURE.md — MateMail System Architecture

**Product:** MateMail
**Owner:** NetaMate Solutions
**Version:** 2.0 — integrated mail platform
**Last updated:** 2026-09-10
**Supersedes:** v1.0 (2026-05-29), which described a Stalwart-based engine that was never built.

---

## Overview

MateMail is a complete, multi-tenant business email platform. It is one product,
not a control panel bolted onto a third-party mail server.

MateMail uses the mature Postfix / Dovecot / Rspamd stack — provisioned and
orchestrated via mailcow — as its **internal Mail Engine**. That engine is an
implementation detail of MateMail, in the same way PostgreSQL is an
implementation detail: essential, replaceable, and invisible to customers.

### The governing principle

> **Customers must never need to know the Mail Engine exists, what it is built
> from, or who makes it.**

Every customer operation — onboarding a domain, creating a mailbox, releasing a
quarantined message, reading mail — happens through MateMail's own UI and API.
No mailcow branding, admin UI, API shape, hostname, container name, or error
string may reach a customer.

---

## Layer model

```
                          ┌─────────────────────────────┐
   Customer ──── HTTPS ──▶│      MateMail Next.js UI    │
                          └──────────────┬──────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │   MateMail Django API       │  ◀── owns ALL product logic
                          │   tenants · RBAC · plans    │
                          │   quotas · onboarding ·     │
                          │   DNS/DKIM · abuse · audit  │
                          └──────────────┬──────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │      MailEngineAdapter      │  ◀── the ONLY boundary
                          │      (the port)             │
                          └──────────────┬──────────────┘
                                         │  internal network only
                          ┌──────────────▼──────────────┐
                          │   MateMail Mail Engine      │
                          │   Postfix · Dovecot ·       │
                          │   Rspamd · DKIM · storage   │
                          │   (mailcow-orchestrated)    │
                          └─────────────────────────────┘
```

Nothing above the adapter may reference the engine's implementation. Nothing
below it may contain MateMail product logic (tenancy, plans, permissions).

---

## What MateMail owns

These are MateMail's responsibility and must never be delegated to the engine's
own interfaces, even where the engine offers an equivalent feature:

| Concern | Owner |
|---|---|
| Tenancy and workspace model | MateMail |
| Roles and permissions (RBAC) | MateMail |
| Plans, quotas, limit enforcement | MateMail |
| Domain onboarding and ownership verification | MateMail |
| Customer-facing DNS instructions and health scoring | MateMail |
| DKIM capability: UI, DNS guidance, verification, status, rotation workflow, audit | MateMail |
| DKIM **private key** generation and storage | Mail Engine (DEC-007r) — MateMail reads only the public key |
| Mailbox / alias / forwarding lifecycle | MateMail |
| Suspension and reactivation | MateMail |
| Anti-abuse policy and rate limiting | MateMail |
| Audit logging | MateMail |
| Queue and quarantine **presentation and actions** | MateMail |
| Backups and restore | MateMail |
| Monitoring and alerting | MateMail |
| Webmail experience | MateMail |
| All customer UI | MateMail |

The engine provides mechanism — message transport, delivery, storage,
authentication, spam scoring, signing. MateMail provides every policy decision
about who may do what, and every pixel a customer sees.

### Two mailbox models (DEC-015 — the second is roadmap, not built)

Every row above is written for **business** mailboxes, where the customer owns
the domain and proves it:

```
rizwan@netamate.com        customer owns the domain, ownership verified
```

DEC-015 adds a second model at P7.5, where **MateMail owns the domain** and a
user claims a name on it:

```
rizwan@matemail.online     MateMail owns the domain, user claims a username
```

The engine sees no difference — a domain is a domain, and no adapter or boundary
change is anticipated. The architectural difference is entirely on MateMail's
side of the port, and it is one row in the table above:

| | Business | Free |
|---|---|---|
| Who owns the domain | the customer | MateMail |
| What authorises a mailbox | proof of domain control | claiming an unused, unreserved username |

That replaces MateMail's strongest authorisation gate with its weakest, which is
why "Domain onboarding and ownership verification" cannot simply be skipped for
free accounts — it has to be *substituted* by username policy, reserved names
and signup abuse controls. Those are designed in P5 and built in P7.5; see
`SECURITY.md` for why the existing abuse table does not transfer unchanged.

The platform's own transactional sender is a third identity and stays separate
from both: `noreply@mail.matemail.online` (DEC-013).

---

## Deployment topology

Everything runs on the existing **MateServer**. There is no separate mail VPS.

```
MateServer (Ubuntu 26.04 LTS)
│
├── Host-native nginx ─── owns :80 / :443 for all NetaMate apps
│   ├── matemail.online          → MateMail public site
│   ├── portal.matemail.online   → MateMail Workspace (customers)
│   ├── platform.matemail.online → Platform Console (NetaMate staff)
│   └── postbox.matemail.online  → PostBox (webmail, mailbox users)
│
├── MateMail application stack (Docker Compose)
│   ├── frontend   127.0.0.1:3020   Next.js
│   ├── backend    127.0.0.1:8020   Django + gunicorn
│   ├── celery-worker / celery-beat (backend image)
│   ├── postgres   no host port     MateMail's own instance
│   └── redis      no host port     MateMail's own instance
│
└── MateMail Mail Engine (separate Compose project)
    ├── Postfix   :25 :587 :465     public mail ports
    ├── Dovecot   :143 :993         public mail ports
    ├── Rspamd, DKIM signing, mail storage
    └── engine admin UI ── NEVER published; operator access via localhost/VPN only
```

Constraints inherited from the NetaMate production model (see `CLAUDE.md`):

- MateMail must not reuse PostgreSQL or Redis belonging to other applications.
- Internal datastores publish no host ports.
- Public web containers bind unique `127.0.0.1` ports — **8020 / 3020** for MateMail.
- Ports through 8016 / 3015 are already allocated; 8015 / 3015 belong to MateConnect.
- Persistent data uses named Docker volumes; `restart: unless-stopped`; healthchecks required.
- Application images are built in CI and pulled from GHCR by commit SHA. The VPS
  does not build source and does not require a Git checkout of the app repo.

The engine's mail ports (25/587/465/143/993) are the one public surface below the
adapter. They are protocol endpoints, not product surfaces: customers configure
them as `smtp.matemail.online` / `imap.matemail.online`, never by an engine name.

---

## The adapter boundary

`apps/mail_engine/adapter.py` defines the port; `mailcow_adapter.py` implements it.
`factory.get_adapter()` selects the implementation from `MAIL_ENGINE_ADAPTER`.

Rules:

1. **No module outside `apps/mail_engine/` may call the engine.** Everything goes
   through `get_adapter()`.
2. **No engine-shaped data crosses the port outward.** Engine IDs, response
   payloads, hostnames and error text are translated to MateMail types before
   returning.
3. **No MateMail product logic lives below the port.** The adapter must not decide
   which forwarding rules are active or what a plan permits; it receives an
   instruction and carries it out.
4. **Errors are typed**, so callers can distinguish "already exists" from
   "engine unreachable" from "rejected", and retry appropriately.
5. **Customer-visible text never originates from the engine.** Engine messages go
   to logs; customers receive MateMail-authored messages.

A `StubAdapter` implements the same port with no engine present. It is the
default, so local development and CI never require the engine. Both
implementations are held to one shared contract suite
(`tests/test_adapter_contract.py`), which is what stops them drifting.

**Implemented in P1.** The port takes frozen DTOs (`apps/mail_engine/dto.py`),
returns `None` or DTOs, and raises typed errors (`apps/mail_engine/errors.py`)
instead of returning a result object with a status string. Mutating operations
are idempotent with the guarantees documented in `adapter.py`, so a Celery
retry converges rather than duplicating. `str()` of every error type is the
MateMail-authored customer message, so engine detail cannot leak even through
careless handling.

---

## Data ownership

MateMail's PostgreSQL is the system of record for tenants, users, domains,
mailboxes, aliases, forwarding rules, plans, subscriptions, audit logs, backup
jobs, and the DNS health model.

The engine keeps its own internal database. MateMail **never writes to it
directly** — only through the adapter. Where the same fact exists on both sides
(a mailbox's existence, a domain's active flag), MateMail's copy is
authoritative and a reconciliation task detects drift.

Facts that only the engine can know — queue contents, quarantine contents,
storage consumed, last login — are pulled into MateMail's database by a sync
task, mapped to a tenant on the way in, and served to customers from MateMail's
own tables. Customer requests never fan out to the engine synchronously.

---

## Request paths

**Control-plane write** (create a mailbox):

```
UI → Django API → permission + plan check → MateMail DB write
                → MailEngineAdapter.provision_mailbox()
                → engine
                → audit log
```

**Mail submission** (customer sends mail): the client authenticates to the
engine's submission port; the engine consults MateMail's internal policy service
before accepting, so suspension, sender identity and rate limits are enforced by
MateMail, not by engine configuration.

**Webmail** is MateMail PostBox, built in-house and served from
`postbox.matemail.online`. It reads and writes mail over IMAP against the
engine's Dovecot and sends through the same authenticated submission path as
any other client, so every policy above applies to it unchanged. No
third-party webmail is used or exposed.

PostBox reaches Dovecot over IMAPS through the engine's TCP gateway rather
than by joining the engine network, which would also expose unauthenticated
LMTP to the application (DEC-050). A mailbox is opened with a Dovecot master
identity scoped to the signed-in address; the person's own password is used
once, at sign-in, and never stored (DEC-051).

PostBox is a third front door, not a section of the Workspace. A Workspace
login does not open a mailbox and never could — administering a mailbox and
reading it are different powers (DEC-049).

**New-mail push** (native PostBox apps, DEC-058): the event starts inside the
engine, after delivery, and never on the mail path.

```
Dovecot LMTP commits the message
  → push_notification Lua hook (LMTP only; 1 s, fail-open)
  → Native API /v1/dovecot/push      (engine network, its own secret)
  → relay thread → MateMail /api/internal/postbox/push-events/
                                     (matemail_engine_link, its own secret)
  → event stored once → Celery → FCM / WNS → PostBox app
```

The push carries identifiers only (event id, device registration id, folder,
UIDVALIDITY, UID). The app fetches anything else through its authenticated
PostBox session. Dovecot joins no new network and holds no MateMail
credential. See `docs/POSTBOX_REMOTE_PUSH.md`.

---

## Security architecture

Detailed controls live in `SECURITY.md`. The architectural invariants:

- Tenant isolation is enforced at the query layer (`TenantScopedManager`) and at
  the permission layer, and is covered by tests at both the ORM and HTTP layers.
- The engine admin interface is never published. Operator access is via localhost
  or VPN only, and every routine operation should eventually be available through
  MateMail's own platform-admin UI so operators do not need it.
- Internal service endpoints live under `/api/internal/`, are denied at the edge,
  and authenticate with a shared secret compared in constant time.
- Secrets (DKIM private keys, TOTP secrets, API keys, internal secrets) never
  appear in APIs, admin forms, logs, frontend bundles or error messages.
- Secret material lives in exactly one component: the one that needs it to do
  its job. The DKIM signing key belongs to the Mail Engine and must not be
  copied into the control plane (DEC-007r). MateMail currently violates this and
  the migration is tracked as debt.

---

## Replaceability

The adapter exists so the engine can be replaced without touching product code.
That property is only real if it is maintained deliberately: every time engine
behaviour leaks upward — an engine ID in a serializer, an engine error string in
a response, product logic inside the adapter — replaceability erodes.

Current known erosions are tracked in `PROJECT_STATUS.md` under the Mail Engine
integration review.
