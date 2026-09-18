# NATIVE_MAIL_ENGINE.md — MateMail Native Engine Migration (NE0–NE8)

**Status:** **NE0 COMPLETE** · **NE1 IN PROGRESS** (2026-09-13) — foundation built and
locally validated; MateServer runtime pending CI-published images. NE2–NE8 not started. mailcow remains the live production engine.
**Product decision: the Private Beta runs on the Native Engine, not on mailcow.**
**Track:** separate from the P0–P9 product roadmap. The P phases are not renumbered.
**Last updated:** 2026-09-13

This document plans the replacement of mailcow as MateMail's Mail Engine
orchestrator with a MateMail-native stack built directly on Postfix, Dovecot and
Rspamd.

It is analysis and sequencing only. No engine code exists, no production change
is proposed here, and mailcow is a live production dependency that must not be
modified or removed on the strength of this document.

---

## 1. Current architecture

```
Next.js frontend ──► Django control plane ──► MailEngineAdapter (port)
                                                      │
                                              MailcowAdapter
                                                      │  HTTPS, API key
                                              HAProxy gateway (10.244.0.247)
                                                      │
                                              mailcow-dockerized 2026-07b
                                                ├── Postfix    (SMTP, submission)
                                                ├── Dovecot    (IMAP, SASL, LMTP)
                                                ├── Rspamd     (spam, DKIM signing)
                                                ├── MySQL      (engine's own state)
                                                ├── Redis      (engine's own state)
                                                └── nginx      (engine API)
```

Two links, both private, both established in P4:

- **MateMail → engine**: the HAProxy gateway on `matemail_engine_link`
  (`internal: true`). No host port is published (DEC-014).
- **engine → MateMail**: the SMTP policy bridge sidecar (P5), which translates
  Postfix's `check_policy_service` protocol into MateMail's internal HTTP API.

MateMail owns: tenancy, approval, plans, quotas, sending limits, suspension,
sender authorization, audit. mailcow owns: the protocols, the mail store, spam
scanning, and the DKIM private keys (DEC-007r).

---

## 2. Why mailcow exists today

DEC-001 chose a mailcow-based foundation over Stalwart and over a hand-rolled
Postfix/Dovecot stack. The reasoning still holds and is worth restating,
because the native migration deliberately gives part of it up:

- Postfix + Dovecot + Rspamd is the most proven open-source mail stack.
- mailcow supplies the orchestration, configuration generation, SQL schemas,
  TLS wiring and upgrade path for all of it.
- It has a REST API covering every provisioning operation MateMail needs.
- The repository was empty at the time; there was no prior work to preserve.

**What mailcow actually provides is not SMTP.** It is *the glue*: the SQL views
Postfix queries for virtual domains and mailboxes, the Dovecot auth
configuration, the Rspamd DKIM key management, the certificate plumbing, the
container topology, and a tested upgrade procedure. That glue is what NE has to
rebuild, and underestimating it is the main risk in this document.

**mailcow is not obsolete and is not deprecated by this plan.** It is a live
production dependency and remains the engine until NE8 explicitly retires it.

---

## 3. Target native architecture

```
Next.js frontend ──► Django control plane ──► MailEngineAdapter (port, UNCHANGED)
                                                      │
                                            NativeMailEngineAdapter
                                                      │  private network
                                              MateMail Native Engine
                                                ├── Postfix    (SMTP, submission)
                                                ├── Dovecot    (IMAP, SASL, LMTP)
                                                ├── Rspamd     (spam, DKIM signing)
                                                ├── provisioning API / config generator
                                                └── PostgreSQL-backed lookups
```

Final target state:

| | Target |
|---|---|
| mailcow dependency | none |
| mailcow Docker stack | none |
| mailcow API | none |
| mailcow UI | none |
| mailcow-specific configuration | none |
| MateMail branding | 100% |
| Control plane | MateMail, authoritative |
| Engine | MateMail native, authoritative |

**We do not write SMTP, IMAP or spam filtering.** Postfix, Dovecot and Rspamd
remain. What MateMail builds is the orchestration, configuration generation,
provisioning and lifecycle around them — the layer mailcow occupies today.

### One design choice NE0 must settle first

mailcow backs Postfix and Dovecot lookups with **MySQL**. MateMail's control
plane is **PostgreSQL**. The native engine has three options, and the choice
shapes every later phase:

| Option | Shape | Trade |
|---|---|---|
| **A** Direct SQL views | Postfix/Dovecot query MateMail's Postgres directly via `pgsql:` maps | Fewest moving parts, no sync lag. Couples the engine to the control-plane schema and puts the mail path on the app's database — an app migration could stop mail. |
| **B** Engine-owned database | Native engine keeps its own Postgres; the adapter writes to it | Clean ownership boundary, mirrors today's model. Adds a sync surface and a second source of truth. |
| **C** Generated flat files | Adapter regenerates Postfix/Dovecot maps on change | No database in the mail path at all. Regeneration and reload become the reliability problem. |

**This is the single most consequential decision in NE and belongs to NE0.**
The current architecture is effectively B, which argues for B on continuity
grounds — but A removes an entire class of drift that P7's reconciliation pass
exists to detect. Do not pick this by default.

---

## 4. What current work is reusable

Measured, not assumed. Every Mailcow mention in `backend/` outside three files
is a **comment or docstring**:

| File | Mentions | Nature |
|---|---|---|
| `apps/mail_engine/mailcow_adapter.py` | 5 | the implementation — 767 lines |
| `apps/mail_engine/factory.py` | 4 | `if name == "mailcow": …` — 3 lines of selection |
| `apps/mail_engine/checks.py` | 1 | one string comparison, "is a real engine configured" |
| `apps/mail_engine/adapter.py` | 1 | docstring |
| `apps/mail_engine/dto.py` | 1 | comment |
| `apps/mail_engine/stub_adapter.py` | 1 | comment |
| `apps/accounts/mailer.py` | 1 | comment |
| `apps/forwarding/services.py` | 1 | comment |
| `config/settings/base.py` | 1 | comment |

**The port boundary held.** The entire functional mailcow surface inside the
Django application is one adapter plus about four lines of selection logic. That
is the P4A investment paying off, and it is why this migration is an adapter
swap rather than a rewrite.

Everything below survives the migration untouched:

```
users / accounts / 2FA / sessions     tenants / workspaces / memberships
admin approval (P5)                   plans and billing models
domains + ownership verification      mailboxes / aliases / forwarding models
quota and storage policy              sending limits (plan-driven)
suspension and outbound-disable       audit logs
abuse controls                        internal SMTP policy API
platform administration               Celery tasks, Postgres, Redis
Next.js frontend                      Django control plane
MailEngineAdapter port + DTOs         policy bridge concept
sender authorization policy           rate-limit policy
```

**The new engine adapts to MateMail. MateMail is not redesigned around the
engine.** If NE work starts proposing changes to tenancy, policy or the DTOs,
that is a signal the native design is wrong, not that MateMail needs changing.

### The contract a native engine must satisfy

`MailEngineAdapter` declares **26 abstract methods**. A native adapter must
implement all of them and pass `tests/test_adapter_contract.py` (48 tests),
which both existing adapters already satisfy:

```
ensure_domain            set_domain_active        delete_domain
list_domains             ensure_mailbox           set_mailbox_active
set_mailbox_password     set_mailbox_quota        delete_mailbox
list_mailboxes           set_mailbox_rate_limit   get_mailbox_rate_limit
clear_mailbox_rate_limit get_mailbox_usage        get_last_login
ensure_alias             delete_alias             ensure_forwarding
get_dkim_public_key      rotate_dkim_key          delete_dkim_key
get_queue_status         get_quarantine_items     cancel_queue_message
release_quarantine_item  check_health
```

Plus the port's documented semantics, which are as much a part of the contract
as the signatures: every mutating call is idempotent except `rotate_dkim_key`;
`delete_*` succeeds on an absent object; errors are typed `MailEngineError`
subclasses; no engine-shaped data crosses outward.

---

## 5. What is mailcow-specific

Nothing here is removed now. This is the map.

### Application code

| Item | Disposition |
|---|---|
| `MailcowAdapter` (767 lines) | **replace** — native equivalent, both coexisting |
| `factory.py` adapter selection | **refactor** — add `"native"` alongside `"mailcow"` |
| `checks.py` `_using_real_engine()` | **refactor** — treat `native` as a real engine too |
| `MAIL_ENGINE_API_URL` / `_API_KEY` | **replace** — native engine's own auth model |
| `MAIL_ENGINE_ADAPTER` | **keep** — this is the switch that makes NE5 reversible |

### Deployment assets (`deploy/engine/`)

| Asset | Disposition |
|---|---|
| `docker-compose.override.yml` | **replace** — native stack has its own Compose project |
| `haproxy.cfg` (gateway) | **replace or remove** — depends on NE0's topology choice |
| `postfix-extra.cf` | **replace** — native Postfix config is generated, not overridden |
| `vars.local.inc.php` | **remove** — a mailcow UI setting; no native equivalent |
| `certbot-deploy-hook-mailcow-mx.sh` | **refactor** — TLS still needed, paths change |
| `nginx-mx.matemail.online.conf` | **keep/refactor** — ACME challenge landing, engine-agnostic |
| `matemail_engine_link` network | **keep** — the private-link pattern survives (DEC-014) |

### Scripts

| Script | Disposition |
|---|---|
| `scripts/install-policy-bridge.sh` | **refactor** — native engine ships policy natively |
| `scripts/postfix_policy_bridge.py` | **keep, largely unchanged** — it speaks Postfix's protocol, not mailcow's |
| `scripts/apply-mailcow-config.sh` | **remove later** — mailcow banner/hostname fixes only |

`postfix_policy_bridge.py` is worth calling out: it contains **no mailcow
knowledge at all**. It translates `check_policy_service` to MateMail's HTTP API.
Under a native engine it moves from sidecar-in-mailcow's-project to
component-of-ours, and the code barely changes.

### Tests

| Module | Tests | Disposition |
|---|---|---|
| `test_adapter_contract.py` | 48 | **keep** — the native adapter must pass it unchanged |
| `test_engine_capabilities.py` | 37 | **keep** — engine-neutral capability assertions |
| `test_engine_policy_integration.py` | 54 | **refactor** — asserts mailcow's restriction chain |
| `test_engine_gateway_assets.py` | 43 | **refactor** — asserts the mailcow-hosted gateway |
| `test_engine_config.py` | 16 | **refactor** |

### Engine-held state that must migrate

| State | Lives in | Migration concern |
|---|---|---|
| Mail store | `mailcowdockerized_vmail-vol-1` | the real payload; NE6's hard problem |
| Mailbox credentials | mailcow MySQL | hashes; MateMail does not hold them |
| DKIM private keys | Rspamd | **must not be casually rotated** — reputation |
| Queue | `postfix-vol-1` | must be drained, not migrated |
| Spam training | `rspamd-vol-1` | losing it degrades filtering quality |

### Production infrastructure identity

`mx.matemail.online`, `169.58.114.252`, the PTR record, SPF, the `mm1` DKIM
selector and DMARC are **product identity, not mailcow's**. They carry the
sending reputation earned in P4 and transfer to the native engine unchanged.
Treat them as the most valuable thing in the migration.

---

## 6. NE0–NE8

### NE0 — Architecture and dependency inventory

**Goal** — Know exactly what mailcow does for us, and decide the native design
before writing any of it.

**Major work** — Complete this inventory against the running engine, not just
the repository. Settle the lookup-backend choice (§3, options A/B/C). Define:
the `NativeMailEngineAdapter` contract mapping to the 26 methods; the engine
service topology; data ownership boundaries; the authentication model (how
Dovecot verifies a mailbox password when MateMail does not store it); the
mailbox storage model and on-disk layout; the queue model; the DKIM lifecycle
honouring DEC-007r; the TLS model; configuration generation and reload; the
upgrade strategy for Postfix/Dovecot/Rspamd without mailcow's; the rollback
strategy; the test strategy. Enumerate every mailcow SQL map Postfix and Dovecot
currently query, because each one is a lookup the native engine must answer.

**Acceptance gate** — A written design a second engineer could implement from,
with the lookup-backend decision made and justified, and a DEC raised recording
it. Every one of the 26 adapter methods has a named native mechanism.

**Production impact** — None. Analysis only.

**Rollback point** — N/A; nothing is changed.

---

### NE1 — Native engine foundation alongside mailcow

**Goal** — A native stack that starts, stays up, and touches nothing live.

**Major work** — `deploy/native-engine/` as its own Compose project: Postfix,
Dovecot, Rspamd, and whichever lookup backend NE0 chose. Private networks of its
own. Digest-pinned images. Healthchecks and `restart: unless-stopped`.
Configuration templates. Secrets handled as in the existing deployment — never
in the repository.

**Acceptance gate** — The stack starts and is healthy on a non-production host.
**No published ports. No production DNS change. No UFW change. No live mail
routing. Its own volumes — mailcow's live data volumes are never mounted.**
Port allocation provably disjoint from the running engine's.

**Production impact** — None if built off-production. If built on MateServer,
it is additive containers only, with no port binding and no shared volume.

**Rollback point** — `docker compose down` on the native project. Nothing else.

---

### NE2 — Domains, mailboxes, aliases and DKIM

**Goal** — The provisioning surface MateMail's adapter needs.

**Major work** — Create/delete domain; create/delete/suspend/restore mailbox;
create/delete alias; domain and mailbox quotas; DKIM generation, public-key
retrieval, rotation and deletion. Honour the port's idempotency contract: every
mutating operation retry-safe except `rotate_dkim_key`; deleting an absent
object succeeds.

**Acceptance gate** — Every operation is idempotent and verified against the
real native engine. **DKIM private keys never leave the engine (DEC-007r).**
Deleting a domain also deletes its DKIM key — the cross-tenant inheritance
defect measured in P4B must not be reintroduced.

**Production impact** — None.

**Rollback point** — Per-operation; the engine holds no customer data yet.

---

### NE3 — Native Postfix, Dovecot and Rspamd integration

**Goal** — Mail actually flows inside the native engine, with MateMail's policy
authority intact.

**Major work** — Virtual domain/mailbox/alias lookups; SMTP AUTH via Dovecot
SASL; **sender-login authorization** (the native equivalent of mailcow's
`reject_authenticated_sender_login_mismatch` + sender ACL); mailbox storage and
the LMTP delivery path; the Rspamd milter path; DKIM signing; TLS; submission
and inbound listeners; queue management; rate controls; suspension enforcement;
and the MateMail policy hook at the same two stages P5 established —
`smtpd_recipient_restrictions` before `permit_sasl_authenticated`, and
`smtpd_end_of_data_restrictions` for once-per-message counting.

**Acceptance gate** — The effective `postconf` shows the policy hook correctly
ordered and every protection present. The P5 relay matrix (A–L) passes against
the native engine. **MateMail remains the policy source of truth — the engine
asks, it does not decide.**

**Production impact** — None.

**Rollback point** — Configuration is generated; regenerate from the previous
revision.

---

### NE4 — implemented locally (2026-09-19)

**Status: implemented and validated locally. MateServer runtime validation
pending.** `MAIL_ENGINE_ADAPTER` is still `mailcow`.

NE4 turned the working NE3 mail path into something operable, and fixed the two
issues NE3 runtime validation surfaced.

#### Mailbox storage lifecycle

Maildir now lives at `/var/vmail/<domain>/<storage_id>`, where `storage_id` is
minted once per mailbox row and never reused (migration 004). Deleting a mailbox
removes routing and authentication, KEEPS the mail, and records it in
`retired_mailbox_storage`.

Before this, deleting and recreating an address handed the new owner the
previous owner's mail. Proven fixed: after delete and recreate the address
reported 0 messages while the old directory remained on disk and recorded.

Existing rows are backfilled with their current `local_part`, so no live
mailbox's storage moves — SQL cannot move files, and a migration that renamed
every path would orphan the data it exists to protect. The fix still holds for
those rows, because a recreated mailbox is always a new row.

**P6 owns retention and eventual removal of retained storage.**

#### Usage

`get_mailbox_usage` asks Dovecot through its administrative HTTP API, on the
engine network, authenticated with its own key. Not derived from the quota
column (which records what is allowed, not what is used) and not measured by
walking the Maildir (which would make Django read customer mail). doveadm
reports kilobytes; the engine converts to the contract's megabytes once, and
rounds down so a mailbox is never reported fuller than it is.

#### Rate limits

Stored in the engine database, enforced by a Postfix policy service running
inside the Postfix container on loopback. The identity charged is the
AUTHENTICATED login, never the envelope sender — otherwise a mailbox could
spread its traffic across every alias it may send as. Exceeding the limit
returns `4.7.1` through `DEFER_IF_PERMIT`; see DEC-027 for why temporary.

#### Queue and quarantine

Both are the real Postfix queue, read with `postqueue -j` (structured, not
scraped) and manipulated with `postsuper`. Quarantine is the HOLD queue, reached
by an Rspamd marker and Postfix's `milter_header_checks`. The three states stay
distinguishable: `active`/`deferred` are queue traffic, `hold` is quarantine.

Two things were found by running it rather than reasoning about it:

* **`soft reject` cannot be quarantine.** It tempfails the client, so the
  message is never accepted and there is nothing to hold. Quarantine is the
  `add header` band, which accepts.
* **`header_checks` never sees milter-added headers.** The marker arrived in the
  delivered message while the rule had already been evaluated against a message
  without it. `milter_header_checks` exists for exactly this.

#### Deployment

`deploy.sh` hashes each service's configuration and passes the hash in the
environment, so Compose recreates a container exactly when its configuration
changed. This is the fix for NE3's finding that Rspamd ran five days with
superseded rules.

#### Adapter

**26 of 26 implemented, nothing deferred.** All eight NE4 methods were exercised
through `NativeMailEngineAdapter` itself against the running engine, returning
real DTOs — not only through the internal HTTP API.

---

### NE4 — Full isolated validation

**Goal** — Prove the native engine before any real traffic.

**Major work** — Validate, in isolation: SMTP submission and AUTH; IMAP; mailbox
and alias delivery; DKIM signing; SPF-compatible sending identity; DMARC
alignment; Rspamd; queue retry; temporary vs permanent failure classification;
quota enforcement; rate limiting; sender-spoof rejection; cross-tenant
rejection; open-relay rejection; suspension; domain deletion with DKIM cleanup;
TLS; restart and container recovery.

**Acceptance gate** — All of the above pass with **no real customer migration
and no Internet delivery**. Recovery is proven by actually restarting things,
not by inspecting configuration.

**Production impact** — None.

**Rollback point** — N/A; still isolated.

---

### NE5 — Switch the MateMail adapter

**Goal** — MateMail talks to the native engine, reversibly.

**Major work** — Implement `NativeMailEngineAdapter` against the unchanged
`MailEngineAdapter` port. Extend `factory.py` so both adapters coexist:

```
MAIL_ENGINE_ADAPTER=mailcow   # today
MAIL_ENGINE_ADAPTER=native    # after NE5
```

**Do not rewrite the rest of MateMail because the engine changed.** If a change
outside `apps/mail_engine/` becomes necessary, stop and re-examine the native
design.

**Acceptance gate** — `test_adapter_contract.py` passes unchanged for the native
adapter. Both adapters selectable. Switching back is one environment variable
and a restart.

**Production impact** — Configuration only, and only when deliberately switched.

**Rollback point** — **The strongest in the whole migration**: set
`MAIL_ENGINE_ADAPTER=mailcow` and restart. This is why both adapters must
coexist rather than one replacing the other.

---

### NE6 — Migrate the platform sender

**Goal** — Move `noreply@mail.matemail.online` to the native engine without
spending the reputation earned in P4.

**Major work** — Move the mailbox and its authentication. Preserve
`mx.matemail.online`, the IP if the topology keeps it, PTR, HELO, SPF, the DKIM
selector and published public key where safely reusable, DMARC, the 60/hour
engine limit, and the TLS identity.

**Acceptance gate** — The platform sender authenticates and is accepted through
the native path, with SPF/DKIM/DMARC still aligned. **DKIM is not rotated
casually** — a new selector means republishing DNS and a fresh reputation
signal, and it must be a deliberate, planned act if it happens at all.

**Production impact** — **Real.** This is the first phase where production mail
identity moves. Account recovery for every future customer rides on this path.

**Rollback point** — Point the mailer back at the mailcow submission path;
mailcow still holds the mailbox. Keep the mailcow-side mailbox until NE8.

---

### NE7 — Production-grade deliverability and protocol validation

**Goal** — Prove the native engine is safe to be the only engine.

**Major work** — Verify SPF, DKIM and DMARC pass; PTR and HELO correct; TLS
correct; SMTP AUTH and IMAP correct; queue behaviour correct; bounce
classification matching P5's permanent/temporary policy; rate controls correct;
open relay impossible; cross-tenant spoof impossible; platform sender isolated
to its exact identity; customer sender policy correct.

**Acceptance gate** — All protocol and authentication checks pass. **Controlled
external delivery validation only if separately and explicitly authorized.**
Protocol correctness does not imply inbox placement, and this document does not
claim it will — reputation is built over time and volume.

**Production impact** — Real if external validation is authorized.

**Rollback point** — NE5's adapter switch.

---

### NE8 — Remove mailcow

**Goal** — Retire the dependency, last.

**Preconditions, all of them:**

```
native engine production proven
MateMail using NativeMailEngineAdapter
platform sender migrated successfully
all mail protocol tests passing
delivery authentication passing
a rollback period has elapsed with the native engine live
backups verified
restore verified from those backups
no MateMail runtime dependency references mailcow
```

**Major work** — Write a decommission plan covering mailcow containers,
volumes, networks, API credentials, the HAProxy gateway, the policy bridge
installation, configuration and `/opt/mailcow-dockerized`. Take a final
verified backup of the mail store and engine state before anything is removed.

**Acceptance gate** — **Deletion is separately authorized by a human, as its own
action.** It is never a side effect of another phase, never automatic, and never
bundled into a deployment.

**Production impact** — Irreversible.

**Rollback point** — **None after deletion.** That is precisely why it is last
and separately gated.

---

## 7. Acceptance gates summary

| Phase | Gate | Production impact | Rollback |
|---|---|---|---|
| NE0 | design document + DEC, lookup backend decided | none | n/a |
| NE1 | stack healthy, no ports, no shared volumes | none | `compose down` |
| NE2 | idempotent provisioning, DKIM stays inside | none | per-operation |
| NE3 | policy hook ordered correctly, relay matrix passes | none | regenerate config |
| NE4 | full isolated validation, recovery proven | none | n/a |
| NE5 | contract tests pass, both adapters selectable | config only | env var |
| NE6 | platform sender works, auth aligned | **real** | mailcow still holds it |
| NE7 | protocol + deliverability proven | real if authorized | NE5 switch |
| NE8 | every precondition met, **separately authorized** | irreversible | **none** |

---

## 8. Rollback strategy

Each phase must be reversible on its own, and the reversibility gets weaker as
the phases progress — which is the ordering's justification.

- **NE1–NE4** are additive and isolated. Rollback is stopping the native stack.
- **NE5** is the pivot, and deliberately the cheapest to undo: one environment
  variable. Both adapters coexist for exactly this reason. Keep them coexisting
  through a defined soak period.
- **NE6** is reversible only while mailcow still holds the platform mailbox, so
  do not delete it there when the native one starts working.
- **NE7** is reversible via NE5.
- **NE8** is not reversible. Everything before it exists to make NE8 boring.

**Database note:** NE introduces no MateMail schema change by design. If a
native phase proposes one, that is a signal the engine is leaking into the
control plane — re-examine before accepting it.

---

## 9. Security model

The native engine inherits P5's model unchanged, because that model is
MateMail's, not mailcow's:

- **MateMail is the policy authority.** The engine asks; MateMail answers
  `OK`/`REJECT`/`DEFER`. The engine must not embed tenancy, approval or
  suspension logic.
- **The policy service answers `DUNNO`, never `OK`** — `OK` would stop Postfix
  evaluating and skip `reject_unauth_destination`, the anti-relay check.
- **Fail closed.** Unreachable policy defers mail; it never passes it unchecked.
- **Defence in depth survives.** The engine keeps its own sender-login check,
  relay restrictions and per-mailbox rate limit. These overlap MateMail's
  deliberately; a native engine that drops them because "MateMail checks that"
  is a regression.
- **DKIM private keys never leave the engine** (DEC-007r).
- **No public mail port opens** at any NE phase without its own authorization.
- **Secrets** stay out of the repository, root-only on the host, never printed.
- **The private-link pattern (DEC-014) is retained**: no host-published socket
  for anything on the mail path, because a published bind address selects a
  destination, never a permitted source.

---

## 10. Deliverability considerations

The single largest risk in this migration is not downtime. It is **silently
degrading a sending reputation that took real mail to earn.**

- `mx.matemail.online`, the PTR, SPF, the `mm1` DKIM selector and DMARC are
  product identity. They transfer; they are not rebuilt.
- **Do not rotate DKIM as part of the migration** unless there is a specific
  reason. A new selector republishes DNS and resets a signal receivers use.
- DMARC stays at `p=none` until there is monitoring to read the reports —
  tightening it during an engine migration would be the worst possible timing.
- Rspamd's **spam training data** (`rspamd-vol-1`) is accumulated knowledge.
  Decide in NE0 whether to migrate or accept the loss; do not discover it at
  NE8.
- P4's successful Gmail delivery proved the authentication chain is correctly
  configured. It did **not** prove future inbox placement, and NE7 must not
  claim otherwise.
- Engine migration is exactly when outbound anomalies are most likely and least
  visible. This is the argument for having P7's monitoring in place first.

---

## 11. Storage and backup implications

The mail store is `mailcowdockerized_vmail-vol-1`. A native engine has its own
volume with its own layout. This has a direct consequence for P6:

- The **technique** (volume snapshot / sync to encrypted offsite storage,
  retention, restore drill) transfers unchanged.
- The **paths and layout** do not.

So backup tooling built for P6 should take the mail-store location and layout as
**configuration, not hard-coded values**. Done that way, the NE cost is changing
a setting and re-running the restore drill, rather than rebuilding the tooling.
Done badly, P6's highest-stakes deliverable gets written twice.

`apps/backups/` currently contains **zero** engine references — it is pure
control-plane scaffolding. That is a good starting position and worth keeping.

---

## 12. Relationship with P6–P9

Analysed against the repository rather than assumed. **No P phase is renumbered
and none is replaced by an NE phase.**

### P6 — backups, restore, safe deletion

Splits cleanly:

| Half | Engine coupling |
|---|---|
| soft-delete + 30-day hold, `pg_dump`, retention, restore runbook, removing the false-green Backups page | **none** — `apps/backups/` has no engine reference |
| mail-store backup and restore | **real** — depends on the engine's volume and layout |

### P7 — operational surface

Mostly survives an engine swap, and this is the important finding: the sync task
reads `get_queue_status`, `get_quarantine_items`, `get_mailbox_usage` and
`get_last_login` — **all four are already in the 26-method adapter contract.**
Written against the port, P7's sync works against any conforming engine.

Engine-specific within P7: per-domain/per-mailbox spam policy (Rspamd's
vocabulary) and blocklist monitoring.

### P7.5 — MateMail Free

Product-level and engine-agnostic. It needs domain and mailbox provisioning,
which the adapter already abstracts. Its real dependencies are P5's policy
framework (done) and P7's monitoring (DEC-015), not the engine.

### P8 — webmail

Speaks IMAP. Dovecot serves IMAP under both engines, so a correctly built
webmail is insulated from the migration — provided it talks IMAP and not
anything mailcow-specific.

### P9 — public launch

Depends on everything. The question P9 asks is which engine the platform
launches on, and that is a business decision, not a technical one.

---

## 13. Recommended execution order

**The central question is whether the Private Beta launches on mailcow or waits
for the native engine.** The recommendation below assumes it launches on
mailcow. If that assumption is wrong, the ordering changes materially and this
section should be revisited.

```
1. Finish P5 production re-activation and close P5.        ← immediate
2. NE0 — architecture and inventory.                        ← may run in parallel
3. P6 against the current engine, with the mail-store
   path as configuration rather than hard-coded.
4. P7 — operations and monitoring.
5. Decide NE1+ placement using NE0's findings.
6. P7.5, P8, P9 as the product roadmap already sequences.
```

**Why finish P5 first.** It is half-activated: the application release is
deployed and its migrations applied, but the engine-side policy hook was rolled
back. A half-activated phase is the worst state to build on, and the remaining
work is one re-activation with a now-hardened installer.

**Why NE0 can run in parallel.** It changes nothing. It is reading, measuring
and deciding. Starting it early means the P6 backup tooling can be designed with
the native storage model already in view.

**Why P6 before NE1, against mailcow.** Three reasons from the evidence: the
control-plane half is entirely engine-agnostic; the mail-store half's technique
transfers even though its paths do not; and backups are a Private Beta gate,
whereas the native engine is not. Blocking backups on a multi-phase engine
migration would delay the beta for no safety gain — the opposite, since it would
mean running a beta without proven restore.

**Why P7 before NE1 — the strongest ordering argument here.** Swapping the mail
engine is precisely when outbound anomalies, queue pathologies and reputation
damage are most likely, and they are invisible without monitoring. Doing NE1–NE8
before P7 means validating a new engine with no operational surface to see it
with. P7's sync also goes through the adapter, so it is not wasted work.

**What would change this.** If the decision is that the Private Beta must launch
on the native engine, then NE0–NE5 move ahead of P6's mail-store half and P7.5,
and the beta date moves out by the length of the migration. That is a product
call, and it should be made explicitly rather than by drift.

---

## 14. Explicit mailcow decommission gate

**mailcow is a live production dependency. It is not deprecated by this
document.**

It may be removed only at NE8, only when every precondition in §6 is met, and
only under its own explicit human authorization. Deletion must never be:

- a side effect of another phase,
- bundled into a deployment,
- performed automatically by any script,
- or inferred from the native engine appearing to work.

Until that authorization exists, the correct state of `/opt/mailcow-dockerized`
is **running and untouched**.

---

## Appendix — status

```
NE0  COMPLETE           NE5  not started
NE1  foundation built, MateServer runtime pending images
NE1  not started        NE6  not started
NE2  not started        NE7  not started
NE3  not started        NE8  not started
NE4  not started

Native engine implementation:  none
mailcow:                       live, production, unmodified
```

The architecture decision is recorded as **DEC-019** in `docs/DECISIONS.md`,
raised on NE0 completion as planned.

---

# NE0 — Architecture design (COMPLETE)

**Status:** design complete, 2026-09-13. No implementation. No production change.
**Supersedes §13's sequencing assumption:** the product decision is now that the
**Private Beta runs on the Native Engine**, not on mailcow. §NE0.12 restates the
order accordingly.

Everything above this line is the roadmap as first written and is kept for
history. This section is the authoritative design NE1 implements from.

---

## NE0.1 What mailcow actually provides — measured

Read-only inventory of the pinned engine (`2026-07b`, `02552ffe`) on MateServer.

**Postfix lookups.** Eight SQL maps, all MySQL:

```
mysql_virtual_domains_maps.cf        is this domain ours?
mysql_virtual_mailbox_maps.cf        does this mailbox exist?
mysql_virtual_alias_maps.cf          alias -> destinations
mysql_virtual_alias_domain_maps.cf   domain aliasing
mysql_virtual_sender_acl.cf          may this login use this MAIL FROM?
mysql_virtual_relay_domain_maps.cf   relay domains
mysql_virtual_resource_maps.cf       resource mailboxes (SoGo)
mysql_virtual_spamalias_maps.cf      temporary spam aliases
```

Only the first five are load-bearing for MateMail. The last three serve mailcow
features MateMail does not offer.

**Dovecot.**

```
mail_home     = /var/vmail/%d/%n
mail_location = maildir:~/
vmail uid:gid = 5000:5000,  /var/vmail mode 2755
passdb/userdb = lua (mailcow's own auth script) + passwd-file for master users
quota         = dict:Userquota::proxy::sqlquota   (MySQL-backed)
password hash = BLF-CRYPT (bcrypt)
```

**Rspamd.**

```
DKIM keys   /data/dkim/keys/$domain.dkim   (private, on rspamd-vol-1)
selectors   Redis map "DKIM_SELECTORS", default selector "dkim"
redis       servers = "redis:6379"          (the ENGINE redis, not MateMail's)
```

**TLS.** `smtpd_tls_cert_file=/etc/ssl/mail/cert.pem`, `key.pem` — populated by
the existing external Certbot deploy hook.

**Rate limits.** Engine Redis.

**Schema.** ~60 tables. The mail path needs roughly nine: `domain`, `mailbox`,
`alias`, `alias_domain`, `sender_acl`, `quota2`, `sasl_log`, `quarantine`,
`tls_policy_override`. The rest are SoGo, the mailcow UI, OAuth, 2FA, ACLs and
templates — features MateMail already owns or does not want.

**Volumes.** `vmail-vol-1` (mail), `vmail-index-vol-1`, `postfix-vol-1` (queue),
`rspamd-vol-1` (DKIM + bayes), `mysql-vol-1`, `redis-vol-1`, `crypt-vol-1`,
`clamd-db-vol-1`, plus SoGo volumes.

**The finding that matters:** mailcow's value is the *glue* — the SQL maps, the
Dovecot auth wiring, the DKIM plumbing, the certificate distribution and the
container topology. Roughly nine tables and five lookups carry MateMail's actual
mail path. That is the scope NE1–NE3 must rebuild; the other fifty tables are
not ours to reimplement.

---

## NE0.2 DECISION — engine state model: **Option B**, engine-owned PostgreSQL

The engine keeps its own PostgreSQL. MateMail's control plane remains
authoritative; the engine database is a **projection**. The write path is
precise and has three distinct roles:

```
MateMail                   is authoritative for all product state
NativeMailEngineAdapter    is the only EXTERNAL provisioning client
matemail-native-api        is the only DIRECT writer to the engine database
```

Nothing else mutates engine state. Postfix and Dovecot hold **read-only**
credentials to the engine database; Django never connects to it at all; the
adapter reaches it exclusively through `matemail-native-api`.

**Why not Option A (Postfix/Dovecot query MateMail's Postgres).** Two reasons,
either sufficient:

*Availability.* P5 deliberately put MateMail's availability into the mail path
for **policy decisions**, and made that fail closed (defer). Extending it to
*every lookup* — does this domain exist, does this mailbox exist, what is its
password — means a routine Django migration holding a lock stops inbound mail
for existing mailboxes. That is a categorically worse failure than deferring a
policy question.

*Blast radius.* Postfix and Dovecot are the Internet-facing, least-trusted
processes in the system. Option A hands them credentials to the database holding
users, sessions, API keys and billing. Today mailcow's MySQL holds mail data and
nothing else. Widening that boundary to save a synchronisation surface is a poor
trade.

**Why not Option C (generated map files).** Every provisioning operation becomes
generate-then-reload, with atomicity and staleness to solve, and `list_domains`
/ `list_mailboxes` / `get_mailbox_usage` become file parsing. Reloading Postfix
on every mailbox creation is not an operational model to choose deliberately.

**The cost of B, and the mitigation.** Two stores can disagree. Mitigated by:
MateMail is authoritative and the engine DB has exactly one direct writer
(`matemail-native-api`, reached only through the adapter); every `ensure_*` is
an idempotent upsert, so re-running converges; and
P7's reconciliation pass — already in the roadmap — is precisely the detector for
this. B is also what runs today, so the operational model is proven rather than
novel.

**Engine DB:** PostgreSQL 16, matching MateMail's version so there is one
database technology to operate, back up and upgrade. Own volume, no host port,
engine network only.

---

## NE0.3 DECISION — authentication

```
Dovecot passdb/userdb -> SQL against the engine PostgreSQL
password scheme       -> BLF-CRYPT (bcrypt), matching mailcow today
```

Choosing bcrypt is not a preference: it is what the platform sender's existing
credential is hashed with, so NE6 can migrate that mailbox **without a password
reset**.

**Separation is preserved and non-negotiable.** A MateMail account password and
a mailbox password remain different credentials. Mail authentication never
consults the Django user table. MateMail stores no mailbox password and no
mailbox hash — `ensure_mailbox(spec, password)` and `set_mailbox_password` pass
plaintext across the adapter exactly as today, and the **engine** hashes it. Any
future SSO is a separate, explicitly justified decision, not a side effect here.

**Suspension.** `set_mailbox_active(False)` clears the mailbox's active flag, so
Dovecot's passdb returns no result and authentication fails outright — the
mailbox cannot log in at all, which is stronger than relying on policy alone.
Tenant suspension is enforced at two layers exactly as P5 established: MateMail's
policy answers REJECT, and `apply_tenant_suspension_task` deactivates the
workspace's domains in the engine.

---

## NE0.4 DECISION — mailbox storage

```
format      Maildir++            mail_location = maildir:~/
home        /var/vmail/<domain>/<local_part>/
uid:gid     5000:5000 (vmail)    /var/vmail mode 2755
indexes     separate volume, /var/vmail_index/<domain>/<local_part>/
quota       maildir quota (maildirsize), NOT a database dict
```

Layout deliberately matches mailcow's. Two payoffs: NE6 can copy the platform
sender's maildir verbatim, and the Dovecot configuration stays conventional
rather than bespoke.

**Quota via maildirsize rather than a SQL dict** — enforcement then has no
database dependency, and a full mailbox stays enforced regardless of engine DB
state. `get_mailbox_usage` is served by the engine API shelling to
`doveadm quota get`, which is a read, not a mail-path dependency.

**No customer mailbox migration is planned**, because the Private Beta starts on
the Native Engine. The only maildir that ever moves is the platform sender's, at
NE6.

---

## NE0.5 DECISION — DKIM (DEC-007r preserved exactly)

```
generation  rspamadm dkim_keygen, executed inside the engine
storage     /data/dkim/keys/<domain>.<selector>.key  on the engine's rspamd volume
permissions 0600, owned by the rspamd runtime user
selector    from DomainSpec.dkim_selector (deployment default mm1)
signing     Rspamd dkim_signing with a selector map keyed by domain
```

**The private key is generated inside the engine and never crosses the adapter.**
There is no method that reads or writes a private key, and none may be added.
MateMail receives only `DkimKeyInfo` — selector, public key, DNS record name and
value — which is public material by definition.

`rotate_dkim_key` is the single non-idempotent operation in the whole contract
and must never be called from a retry path. `delete_dkim_key` is idempotent.

**Domain deletion always deletes the key first.** This is not housekeeping: P4B
measured that a key outlives its domain in mailcow and is inherited by whoever
registers that name next — a cross-tenant private-key leak. The native engine
must not reintroduce it, and NE2's gate tests for it explicitly.

---

## NE0.6 DECISION — aliases and send-as (fixes the P5 defect)

P5's production activation found MateMail authorising alias sending while the
engine rejected it at MAIL FROM, because `ensure_alias` created the alias with
`sender_allowed=0`. Fail-closed, but wrong. The native model separates three
things that mailcow conflates into one table:

| Concept | Meaning | Backing |
|---|---|---|
| **delivery alias** | mail *to* X is delivered to Y | `virtual_alias_maps` |
| **send-as grant** | mailbox Y may use X as MAIL FROM | `smtpd_sender_login_maps` |
| **forwarding** | a mailbox's own mail is copied onward | delivery alias including/excluding the mailbox |

**`ensure_alias` writes BOTH the delivery alias and the send-as grant** for its
destination mailbox. That is the fix, and it is a gate: NE2 is not complete
until an alias created through the adapter can actually be used as MAIL FROM
through `reject_authenticated_sender_login_mismatch`.

Authorisation rules, unchanged from P5:

```
a mailbox MAY send as itself
a mailbox MAY send as an active alias explicitly granted to it
a mailbox MAY NOT send as another mailbox
a mailbox MAY NOT send as any cross-tenant address
a mailbox MAY NOT send as an arbitrary hosted address
```

Postfix's `reject_authenticated_sender_login_mismatch` stays as defence in
depth. MateMail and the engine must agree; where they disagree, mail that should
flow does not, or worse.

---

## NE0.7 DECISION — Postfix policy integration

**Unchanged from P5, deliberately.** It is proven in production as of
2026-09-13 and there is no reason to redesign it.

```
smtpd_recipient_restrictions =
    check_recipient_mx_access ...,
    permit_mynetworks,
    check_policy_service inet:<policy>:10031,     <-- before permit_sasl_authenticated
    permit_sasl_authenticated,
    check_recipient_access ...,
    reject_invalid_helo_hostname,
    reject_unauth_destination                     <-- must stay reachable

smtpd_end_of_data_restrictions =
    check_policy_service inet:<policy>:10031      <-- counts once per message
```

`scripts/postfix_policy_bridge.py` becomes a **first-class service in the native
topology**, not a sidecar bolted into another project's Compose file. The code
barely changes: it speaks Postfix's protocol and MateMail's HTTP API and
contains no mailcow knowledge at all.

Invariants that carry over verbatim: answer `DUNNO` and never `OK` (OK would
skip `reject_unauth_destination`); fail closed with `DEFER_IF_PERMIT`;
`stage=rcpt` authorises without counting and `stage=end_of_data` counts.

---

## NE0.8 DECISION — Rspamd

```
milter        Postfix smtpd_milters -> rspamd:11332
redis         DEDICATED engine Redis, not MateMail's application Redis
state         bayes, fuzzy, ratelimit, DKIM selector map
persistence   own volume
```

**A dedicated Redis is a deliberate boundary, not duplication.** MateMail's
application Redis is a Celery broker and cache with an unrelated lifecycle; a
flush there is routine and would destroy accumulated spam training. Rspamd's
Redis holds learned state that is expensive to rebuild and cheap to isolate.

Per-domain and per-mailbox spam policy is exposed through MateMail's own API
(P7), never through an engine UI — customers must never see the engine.

---

## NE0.9 DECISION — TLS

```
identity     mx.matemail.online (unchanged — it carries the P4 reputation)
issuance     the existing external Certbot on the host
distribution deploy hook copies into the engine and reloads Postfix + Dovecot
paths        native layout under the engine's own config volume
```

The existing certbot deploy-hook model is reused because it already works and is
versioned. What changes is only the destination path: the native engine must not
depend on mailcow's `/etc/ssl/mail/` layout.

---

## NE0.10 DECISION — service topology

Seven services. No public ports at NE1–NE4; no host ports at all unless proven
necessary, and any port claimed must first be verified free with
`ss -tulpn | grep LISTEN`.

| Service | Purpose | Networks | Ports | Volumes | Privileges |
|---|---|---|---|---|---|
| `matemail-native-postfix` | SMTP inbound + submission | engine | none (NE1–NE4) | queue, tls, conf | none |
| `matemail-native-dovecot` | IMAP, SASL, LMTP | engine | none | vmail, vmail-index, tls, conf | none |
| `matemail-native-rspamd` | milter, DKIM signing, spam | engine | none | rspamd (DKIM + bayes) | none |
| `matemail-native-db` | PostgreSQL 16, engine state | engine | none | db | none |
| `matemail-native-redis` | Rspamd + rate-limit state | engine | none | redis | none |
| `matemail-native-api` | provisioning + DKIM lifecycle | engine + `matemail_engine_link` | none | rspamd (DKIM), db access | none |
| `matemail-native-policy` | Postfix policy bridge | engine + `matemail_engine_link` | none | none | none |

All images pinned by tag **and digest**. `restart: unless-stopped`. Healthchecks
on every service. Secrets from the environment, never in the repository.

**Why an engine API service rather than the adapter writing SQL directly.**
DKIM is the deciding argument: key generation and deletion need filesystem
access inside the engine, and DEC-007r forbids the private key crossing the
boundary. Something engine-side must own it. Given that, routing all provisioning
through one authenticated service keeps a single trust boundary and a single
auth model — and makes `NativeMailEngineAdapter` the same *shape* as
`MailcowAdapter`, which is what keeps NE5 a swap rather than a rewrite.

Only `matemail-native-api` and `matemail-native-policy` join
`matemail_engine_link`. Postfix, Dovecot, Rspamd, the database and Redis have no
route to MateMail at all.

---

## NE0.11 Failure behaviour

| Unavailable | Behaviour | Rationale |
|---|---|---|
| MateMail backend | policy DEFERs (`DEFER_IF_PERMIT`) | proven in P5; never permits |
| policy service | Postfix `smtpd_policy_service_default_action = 451 4.3.5` | verified in production; a 4xx, never a permit |
| engine database | auth and lookups fail → mail deferred by the sender | no partial acceptance |
| engine Redis | Rspamd degrades; rate limits fail closed | an unmetered send path is what limits exist to prevent |
| Rspamd | milter unavailable → Postfix defers (`milter_default_action = tempfail`) | never accept unscanned mail silently |
| Dovecot | no auth, no delivery → deferred | |
| Postfix | nothing accepted | |
| disk full / read-only vmail | Dovecot returns temporary failure → sender retries | never lose a message to a full disk |
| certificate expired | submission fails; **monitoring must catch this before it happens** | P7 |
| DNS unavailable | outbound deferred and retried | |

**The invariant across every row: a failure defers mail. No failure mode may
produce an open relay, and none may silently accept mail it cannot scan,
authenticate or deliver.**

---

## NE0.12 Migration from mailcow, and rollback

Scope is small **because the Private Beta starts on the Native Engine** — there
is no customer mailbox migration, now or later.

What moves, at NE6, is one identity:

```
noreply@mail.matemail.online   mailbox + bcrypt credential (no password reset)
                               maildir contents
                               60/hour engine rate limit
mail.matemail.online           DKIM private key and mm1 selector
```

Preserved unchanged: IP, PTR, HELO, SPF, the published DKIM public record,
DMARC, and the `mx.matemail.online` TLS identity. **DKIM is not rotated as part
of the migration** — a new selector republishes DNS and resets a reputation
signal earned by real delivered mail.

**Rollback**, weakening as phases progress:

| Phase | Rollback |
|---|---|
| NE1–NE4 | stop the native stack; it is additive and isolated |
| NE5 | `MAIL_ENGINE_ADAPTER=mailcow` and restart — one variable, both adapters coexist |
| NE6 | point the mailer back at mailcow's submission path; **keep mailcow's copy of the mailbox until NE8** |
| NE7 | NE5's switch |
| NE8 | none — which is why it is last and separately authorised |

---

## NE0.13 Final sequencing

The proposed order is **confirmed**, with reasoning and two added gates:

```
P5 ✅ complete and activated
  ↓
NE0 ✅ this document
  ↓
NE1  native stack alongside mailcow
NE2  domains / mailboxes / aliases / DKIM
NE3  Postfix + Dovecot + Rspamd integration
NE4  full isolated validation
NE5  switch MailEngineAdapter to native
  ↓
P6   backups and restore — written against the NATIVE store
P7   operations and monitoring
  ↓
NE6  migrate the platform sender
NE7  production protocol and deliverability validation
  ↓
P7.5 MateMail Free
  ↓
PRIVATE BETA
  ↓
NE8  remove mailcow (separately authorised)
```

**Why P6 sits after NE5 rather than before.** The Private Beta runs on the
Native Engine, so backup tooling written against mailcow's `vmail-vol-1` would
be rebuilt within weeks. Writing it once, against the store customers will
actually use, is strictly cheaper. `apps/backups/` has no engine reference today,
so nothing is stranded by waiting.

**Why NE6 sits after P6 and P7, not before.** Moving the platform sender moves
the identity that carries the sending reputation. Doing that before backups are
proven and before there is monitoring to see queue and reputation anomalies is
the single riskiest ordering available. NE5 is safe early precisely because
there are zero customers; NE6 is not, because the platform identity is real.

**Two gates added to the phases as written:**

1. **NE5 requires the full P5 policy matrix to pass against the native engine** —
   relay, spoof, approval, suspension, outbound-disable, rate limits, platform
   isolation, fail-closed — not merely `test_adapter_contract.py`. Contract
   conformance proves shape; the matrix proves behaviour.
2. **NE2 requires an alias created through the adapter to be usable as MAIL
   FROM**, verified through `reject_authenticated_sender_login_mismatch`. This is
   the P5 defect; it must fail the phase, not be discovered in production again.

---

## NE0.14 Upgrade strategy

mailcow supplies an integrated upgrade path today. Without it, MateMail owns
upgrades.

```
Postfix / Dovecot / Rspamd   track the stable release of the base distribution;
                             pin tag AND digest; never `latest`
PostgreSQL 16 / Redis 7      pinned by tag and digest, matching MateMail's
                             versions so there is one of each to operate
security updates             rebuild and redeploy the pinned image; the engine
                             is stateless apart from its volumes
config compatibility         every upgrade runs NE4's full isolated validation
                             before it reaches production
schema migrations            engine DB migrations are versioned and forward-only,
                             applied by matemail-native-api at startup
rollback                     redeploy the previous digest; engine schema
                             migrations must be backward compatible for one
                             release, so the previous image runs against the
                             new schema
```

The upgrade burden is real and is a genuine cost of leaving mailcow. It is
accepted deliberately: NE4's validation suite is what makes it manageable, and
building that suite is why NE4 exists as its own phase.

---

## NE0.15 Security boundaries

```
Postfix / Dovecot / Rspamd   no route to MateMail, no MateMail credentials
matemail-native-api          the ONLY direct writer of engine state; DKIM private
                             keys never leave it; shared-secret authenticated
NativeMailEngineAdapter      the only external provisioning client; talks to the
                             api, never to the engine database
Postfix / Dovecot            READ-ONLY database credentials; they look state up,
                             they never mutate it
matemail-native-policy       reads MateMail policy; answers DUNNO, never OK;
                             fails closed
engine database              engine data only — never users, tokens or billing
DKIM private keys            0600, engine-side, DEC-007r, never across the port
mailbox passwords            bcrypt, engine-side; MateMail stores neither the
                             password nor the hash; never the web account password
private link                 matemail_engine_link stays internal: true; no host
                             port on the mail path (DEC-014)
public ports                 none at NE1–NE4; opening one is its own decision
```

---

## NE0.16 Adapter contract — unchanged

All 26 methods map to native mechanisms with **no change to the interface, the
DTOs or the typed errors**. `tests/test_adapter_contract.py` (48 tests) applies
to the native adapter unmodified.

| Method | Native mechanism | State |
|---|---|---|
| `ensure_domain` | api → engine DB `domain` upsert; DKIM key created if absent | DB + rspamd vol |
| `set_domain_active` | `domain.active` flag | DB |
| `delete_domain` | delete DKIM key **first**, then domain rows | DB + rspamd vol |
| `list_domains` | `SELECT` from `domain` | DB |
| `ensure_mailbox` | `mailbox` upsert; bcrypt hash engine-side; maildir created on first delivery | DB + vmail |
| `set_mailbox_active` | `mailbox.active` → passdb returns nothing → auth fails | DB |
| `set_mailbox_password` | re-hash engine-side, update row | DB |
| `set_mailbox_quota` | `mailbox.quota`; Dovecot maildirsize enforces | DB + vmail |
| `delete_mailbox` | remove row, then maildir | DB + vmail |
| `list_mailboxes` | `SELECT`, optional domain scope | DB |
| `set_mailbox_rate_limit` | engine Redis key, Rspamd ratelimit | Redis |
| `get_mailbox_rate_limit` | read that key → `RateLimit` or `None` | Redis |
| `clear_mailbox_rate_limit` | delete the key | Redis |
| `get_mailbox_usage` | `doveadm quota get` via the api | vmail |
| `get_last_login` | read of `mailbox.last_login`, written **only** by `matemail-native-api` via the auth-success hook — see NE0.21 | DB |
| `ensure_alias` | delivery alias **and** send-as grant (NE0.6) | DB |
| `delete_alias` | remove both | DB |
| `ensure_forwarding` | delivery alias for the mailbox's own address | DB |
| `get_dkim_public_key` | read public half of the engine-side key | rspamd vol |
| `rotate_dkim_key` | `rspamadm dkim_keygen`; NOT idempotent | rspamd vol |
| `delete_dkim_key` | unlink key + selector entry; idempotent | rspamd vol |
| `get_queue_status` | `postqueue -j` via the api; engine-wide, caller maps to tenant | queue vol |
| `get_quarantine_items` | Rspamd quarantine store; engine-wide | DB/Redis |
| `cancel_queue_message` | `postsuper -d <id>`; idempotent by id | queue vol |
| `release_quarantine_item` | re-inject the quarantined message | DB/Redis |
| `check_health` | api aggregates per-service health; never raises | — |

**No contract change is required.** Had one been, the instruction was to stop and
explain rather than change it; none is.

---

## NE0.17 DECISION — malware scanning: **both retained**

Read-only production inventory, 2026-09-13:

```
clamd-mailcow   running   Rspamd antivirus:  servers = "clamd:3310", max_size 20 MiB,
                          symbol CLAM_VIRUS, scan_mime_parts = false
olefy-mailcow   running   Rspamd external_services: servers = "olefy:10055",
                          scan_mime_parts = true, max_size 3 MiB, timeout 20s
signatures      current   daily.cld 86 MB, updated the same day it was inspected
volume          mailcowdockerized_clamd-db-vol-1 -> /var/lib/clamav
```

```
ClamAV retained:   YES
Olefy retained:    YES
```

**Why both, explicitly.** MateMail sells business email. Silently shipping a
native engine without the malware scanning the platform has today would be a
security regression customers could not see and did not agree to — the worst
shape a regression can take. Olefy in particular scans OLE/macro content in
Office attachments, which is the dominant malware delivery vector in exactly the
market MateMail serves; dropping it because it is an extra container would be
trading a real defence for a smaller topology.

```
malware scanning path:
    Postfix --milter--> Rspamd --+--> ClamAV (clamd, full message, <= 20 MiB)
                                 +--> Olefy  (OLE/macro parts, <= 3 MiB)

failure behavior:
    scanner unreachable or slow -> Rspamd soft-rejects -> Postfix DEFERS.
    Mail is NEVER accepted unscanned. This matches the engine's current posture
    (`soft_reject_on_timeout = true`, verified in production) and the rule
    already stated in NE0.11: every failure defers, none accepts silently.

persistent state:
    ClamAV signature database, ~175 MB, own volume, refreshed by freshclam.
    Olefy is stateless.

services required:
    matemail-native-clamav   clamd + freshclam
    matemail-native-olefy    OLE/macro scanner
```

**One network consequence worth naming:** `freshclam` needs outbound HTTPS to
fetch signatures. That is the only service in the native engine with a
legitimate need for general egress, and it should be the only one granted it.
Stale signatures are a silent failure, so signature age belongs in P7's
monitoring alongside certificate expiry.

The topology in NE0.10 grows from seven services to **ten** (see NE0.19).

---

## NE0.18 DECISION — DNS resolver: **dedicated validating Unbound, required**

Read-only production inventory, 2026-09-13:

```
unbound-mailcow   running at 10.244.0.254, no published ports
postfix / rspamd / dovecot   HostConfig.Dns = ["10.244.0.254"]
                             (so Docker's 127.0.0.11 forwards to Unbound)
unbound.conf      auto-trust-anchor-file: trusted-key.key, do-ip6: yes
rspamd            dns { enable_dnssec = true; }
postfix           smtp_dns_support_level = dnssec
                  smtp_tls_security_level = dane
validation proof  `unbound-host -v -r dnssec-failed.org` -> SERVFAIL (correct:
                  a validating resolver must refuse a deliberately broken zone)
```

**This is not a preference, it is a dependency.** Postfix is configured for
**DANE**, and DANE derives its security entirely from DNSSEC validation. Point
Postfix at the host resolver or a public resolver that does not validate, and
TLSA lookups silently stop being trustworthy — outbound mail keeps flowing and
the guarantee quietly disappears. That is precisely the class of failure this
architecture is written to avoid.

```
resolver service:      matemail-native-unbound (validating, recursive)
which mail services:   Postfix, Rspamd, Dovecot — all via the Compose `dns:` key
                       pointing at the resolver's static engine-network address
DNSSEC behavior:       validation ON; auto-managed trust anchor; a bogus answer
                       is SERVFAIL, never a downgrade to unvalidated
failure behavior:      resolver down -> MX/TLSA/SPF/DNSBL lookups fail ->
                       Postfix DEFERS outbound, Rspamd soft-rejects inbound.
                       Fail-closed, consistent with NE0.11. Never falls back to
                       an unvalidated resolver: a silent downgrade is worse than
                       a visible deferral.
persistence/cache:     cache in memory. One improvement over the current setup:
                       give the trust anchor its own small volume so it survives
                       a restart instead of re-bootstrapping each time.
network:               engine network only, static address
public exposure:       NONE — no published port, no host binding
```

**Explicitly rejected:** relying on Docker's embedded resolver alone, the host's
`/etc/resolv.conf`, or a public resolver. The first two do not validate; the
third moves a security-critical dependency off-host and hands a third party
visibility into every recipient domain MateMail's customers mail.

---

## NE0.19 Revised service topology — ten services

NE0.10's seven, plus the three NE0.17–NE0.18 require.

| Service | Purpose | Networks | Ports | Egress | Volumes |
|---|---|---|---|---|---|
| `matemail-native-postfix` | SMTP inbound + submission | engine | none | mail | queue, tls, conf |
| `matemail-native-dovecot` | IMAP, SASL, LMTP | engine | none | none | vmail, index, tls, conf |
| `matemail-native-rspamd` | milter, DKIM signing, spam | engine | none | DNSBL via resolver | rspamd (DKIM + bayes) |
| `matemail-native-clamav` | clamd + freshclam | engine | none | **HTTPS (signatures)** | clamav-db |
| `matemail-native-olefy` | OLE/macro scanning | engine | none | none | — |
| `matemail-native-unbound` | validating recursive resolver | engine (static IP) | none | DNS (53) | trust-anchor |
| `matemail-native-db` | PostgreSQL 16, engine state | engine | none | none | db |
| `matemail-native-redis` | Rspamd + rate-limit state | engine | none | none | redis |
| `matemail-native-api` | provisioning + DKIM lifecycle | engine + `matemail_engine_link` | none | none | rspamd (DKIM), db |
| `matemail-native-policy` | Postfix policy bridge | engine + `matemail_engine_link` | none | none | none |

Unchanged from NE0.10: every image pinned by tag **and** digest;
`restart: unless-stopped`; healthchecks throughout; no public ports at NE1–NE4;
any host port claimed only after `ss -tulpn | grep LISTEN` proves it free; and
only `api` and `policy` touch `matemail_engine_link`.

**Egress is now a deliberate column.** Only ClamAV needs general outbound HTTPS,
only Unbound needs outbound DNS, and only Postfix needs to reach the Internet on
25. Everything else should have no route off the host at all.

---

## NE0.20 DECISION — NE6 migration of secrets and keys

Three artifacts move at NE6. **None of them may travel through Django, through
`MailEngineAdapter`, or through this workstation.** Each is an engine-to-engine
transfer performed as root on MateServer, under a deliberate, authorised
maintenance step.

The rules this must not break are pre-existing: DKIM private keys never cross
the port (DEC-007r), and MateMail stores neither mailbox passwords nor their
hashes.

### Shared principles

```
staging            /root/ne6-migration-<UTC stamp>/, mode 0700, root:root
transport          never leaves MateServer; never transits the workstation;
                   never passes through the control plane
logging            no secret value is printed, echoed or logged at any point
verification       by checksum and by PUBLIC material only, never by comparing
                   or displaying a secret
cleanup            staging shredded and removed after verification
reversibility      mailcow keeps its copy of all three until NE8
```

### DKIM private key

```
mailcow rspamd volume   /data/dkim/keys/mail.matemail.online.dkim   (0600)
        |  root-only copy, mode and ownership preserved
        v
root staging            /root/ne6-migration-<stamp>/dkim/            (0700)
        |
        v
native rspamd volume    /data/dkim/keys/mail.matemail.online.mm1.key
                        chown to the native rspamd runtime user, chmod 0600
verify                  compare the PUBLIC half against the published mm1 DNS
                        record — never read, print or diff the private half
cleanup                 shred -u the staging copy
```

The selector stays `mm1` and the published DNS record is unchanged, so no
republication and no reputation reset. If the key cannot be transferred intact,
the correct response is to stop — **not** to generate a new key and rotate DNS
as a workaround.

### bcrypt mailbox hash

```
mailcow MySQL           mailbox.password for noreply@mail.matemail.online
                        (BLF-CRYPT — bcrypt, already the native scheme)
        |  root-run, engine-to-engine SQL transfer on the host; the hash is
        |  handled as an opaque value and never rendered to a terminal
        v
native PostgreSQL       mailbox row for the same address
```

It is **never written to MateMail's control-plane database**, never returned
through the adapter, and never printed. Because both engines use bcrypt, the
credential survives the move unchanged and **no password reset is required** —
which is the entire reason NE0.3 chose BLF-CRYPT rather than a scheme we might
otherwise have preferred.

Verification is behavioural, not by inspection: authenticate once against the
native engine over the private submission path and confirm success. That proves
the hash transferred without ever examining it.

### Maildir

```
mailcow vmail volume    /var/vmail/mail.matemail.online/noreply/
        |  rsync -aHAX --numeric-ids   (ownership, timestamps, ACLs preserved)
        v
native vmail volume     /var/vmail/mail.matemail.online/noreply/
verify                  message count and total size match; Dovecot reindexes
```

`--numeric-ids` matters: both engines use vmail `5000:5000`, and resolving names
across two containers with different passwd files is how ownership silently
becomes `nobody`.

### Cutover and rollback

```
1. native engine healthy, NE4 validation passed, P6 backups and P7 monitoring in place
2. copy all three artifacts as above; verify each
3. authenticate the platform sender against the native engine (no mail sent)
4. switch MateMail's EMAIL_HOST to the native submission path
5. send ONE authorised transactional message and confirm SPF/DKIM/DMARC alignment
6. keep mailcow's copy of mailbox, key and maildir untouched until NE8
```

Rollback at any point before step 6 is to point `EMAIL_HOST` back at mailcow's
submission path; mailcow still holds a complete, working copy. That is why
NE0.12 forbids deleting it at NE6.

---

## NE0.21 DECISION — how `last_login` is recorded

NE0.16 originally said `get_last_login` read "a `sasl_log`-equivalent table
written on successful auth" without saying *who writes it*. Read literally, the
only component present at a successful authentication is Dovecot — which would
have meant giving Dovecot write credentials to the engine database, directly
contradicting NE0.2 and NE0.15. The omission is resolved here.

### The flow

```
successful Dovecot authentication (IMAP or SMTP submission via SASL)
        |
        v
Dovecot post-login / auth-success hook
        |   authenticated request over the engine network
        v
matemail-native-api        POST /internal/auth-event
        |
        v
UPDATE mailbox SET last_login = <ts> WHERE username = <address>
        |
        v
get_last_login  reads mailbox.last_login through the api
```

### Why it is shaped this way

`matemail-native-api` stays the only direct writer to the engine database, so
NE0.2's write model holds without exception. Dovecot and Postfix keep
**read-only** credentials: they answer lookups, they never mutate state. A
compromise of the Internet-facing daemons therefore cannot rewrite engine state,
which is the whole point of separating the roles.

### Requirements this must satisfy

```
Dovecot DB credentials        READ-ONLY (unchanged)
Postfix DB credentials        READ-ONLY (unchanged)
matemail-native-api           the only direct DB writer (unchanged)
endpoint exposure             engine network only; never on matemail_engine_link,
                              never on a host port — this is engine-internal
                              telemetry, and MateMail has no reason to call it
authentication                the same shared-secret model the api already uses
```

### The event carries the minimum, and no credential

```
sent:        mailbox address, timestamp, protocol (imap|submission),
             client IP where the hook provides it
NOT sent:    password, password hash, session token, message content,
             or anything else not needed to record a login
```

There is no case in which a credential belongs in a telemetry event, and the
hook must not be able to carry one even accidentally — the api rejects any field
outside the list above rather than ignoring it.

### Failure must be invisible to the user

**Recording `last_login` must never make a successful authentication fail.** The
hook is fire-and-forget with a short timeout: if `matemail-native-api` is slow,
unreachable or returns an error, the login still succeeds and the failure is
logged engine-side for P7 to surface.

This is the one place in the native design where a failure does **not** defer,
and the asymmetry is deliberate. Everywhere else a failure means MateMail cannot
tell whether mail should flow, so it defers. Here the security decision —
whether this credential is valid — has *already been made correctly* by Dovecot
against read-only data. `last_login` is an operational nicety. Blocking a
customer's mail client because a telemetry write failed would be a self-inflicted
outage in exchange for a timestamp.

Staleness is therefore possible and acceptable: a `last_login` that is a few
minutes old, or missing after an api outage, is a monitoring signal (P7), not a
correctness problem.

---

# NE1 — Foundation (COMPLETE, 2026-09-13)

Implementation lives in `deploy/native-engine/`; see its README for operation.

**Built and locally validated.** All ten services defined; own network
(`matemail_native_engine`, 172.27.0.0/16 — chosen by enumerating the host after
172.26 turned out to belong to `myright_internal`); nine new volumes; no host
ports; no mailcow network or volume referenced anywhere. `db`, `redis`, `api`
and `policy` were started locally, reached healthy, and their state survived a
full stack restart. The Unbound image was built and its DNSSEC validation
proven: signed zones return the `ad` flag, `dnssec-failed.org` returns SERVFAIL.
All four repository-controlled images have since been built and validated
locally.

**Three defects found and fixed during NE1, all of which would have been silent:**

The resolver healthcheck originally used `unbound-host -r`, which reads
`/etc/resolv.conf` and asks *Docker's* resolver — it passes with Unbound
completely dead. Now `drill -D @127.0.0.1` requiring the `ad` flag, so a
resolver that answers without validating also fails.

The network subnet was first set to 172.26.0.0/16 by assumption. That range
belongs to another NetaMate application on the same host. Ports and subnets get
enumerated, never assumed.

The Dovecot placeholder used a `static` passdb with `nopassword=y`, which
accepts *any* credential for *any* user. A passwordless placeholder is not a
placeholder. It is now an empty `passwd-file`, and the denial is proven by
mutation rather than by reading the config: with one BLF-CRYPT account
temporarily added the correct password authenticates and a wrong one does not,
so the lookup is demonstrably live and the empty file is demonstrably what
refuses everyone.

**The vmail identity model is an NE1 foundation property.** NE0.4 fixes the mail
store at vmail 5000:5000 for mailcow compatibility, the NE6 platform-sender
migration and unambiguous ownership. The upstream `dovecot/dovecot:2.4.1` image
ships vmail at 1000:1000, no `dovecot` and no `dovenull`, and runs unprivileged
as vmail — so uid 5000 is unreachable (`setgid(5000) failed with euid=1000`) and
the pre-auth login processes cannot be separated from the mail user
(`fchown() failed for /run/dovecot/login`). Rather than downgrade the
architecture to 1000, the engine builds a thin derivative of the pinned upstream
digest that corrects the identities and leaves the Dovecot binaries
byte-for-byte identical. Master starts as root and drops to `dovenull`
(pre-auth), `dovecot` (internal) and `vmail` (mail) — Dovecot's own model, with
no privileged container, no host networking and no added capabilities. Verified
on the built image: `/run/dovecot/login` is group `dovenull`, the mail worker
creates a maildir whose every file is 5000:5000, and nothing is left owned by
uid or gid 1000.

**Runtime deployed and validated on MateServer (2026-09-13).** The four
repository-controlled images were published to GHCR from commit `a303b7b`
(workflow run 34730345422) and are pinned in production by immutable digest:

```
matemail-native-postfix@sha256:d3aec130fcf42cf8926d278cdbb62944e1ecff8fb8948ae4864745d6e5dde363
matemail-native-dovecot@sha256:5ebd68c8712b1baf0c6a527385b4a2cf2c2abfa5f2144d5a4da87e654e975c73
matemail-native-unbound@sha256:3149f484719d88aca16d2cab6fa4fff084af47ede134258a1cfb7550d3004d8f
matemail-native-olefy@sha256:9a077fe584e821cc8bae7b9607a301a3fbcb8f21260543c8dd589b3b54d69b18
```

All four packages remain private, and the `ne1` tag was checked against the
commit-SHA tag so the digest is known to come from this build rather than
assumed. Runtime lives at `/opt/MateMailNative/` with no git checkout on the
VPS; the `.env` is `0600 root:root` with secrets generated on the server.

**10 / 10 services healthy.** A full-stack restart returned all ten to healthy in
about 30 seconds with PostgreSQL and Redis state intact and all nine volumes
preserved. Isolation was measured, not assumed: every container sits only on
`matemail_native_engine` (172.27.0.0/16, re-verified free immediately before
creation), none joined `mailcow-network`, no mailcow or MateMail-app volume is
mounted anywhere, and nothing is privileged or holds an added capability. No host
port is published — the host `LISTEN` count was 40 before and 40 after, UFW is
byte-identical, and DNS, PTR and MX are unchanged.

Verified against the **deployed** images rather than the local builds: Dovecot
carries vmail 5000:5000 with `dovenull` (998) owning `/run/dovecot/login` and
`dovecot` (999) owning the internal sockets, its passwd-file holds no accounts
and every credential is refused; Postfix runs `inet_protocols = ipv4` with 25 and
587 reachable only inside the container and zero IPv6 sockets; Unbound returns
NOERROR with the `ad` flag for a signed zone and SERVFAIL for a bogus one while
still answering AAAA; ClamAV holds current signatures (1.4.6/28115, `daily.cld`
fetched the same day) on its own volume; Rspamd resolves Redis, ClamAV, Olefy and
Unbound to native 172.27.0.x addresses with no mailcow Redis or DKIM volume.

Resource cost is 1081 MiB across the ten containers — 945 MiB of it ClamAV, which
is memory-bounded — taking available RAM from 5.5 to 4.3 GiB with swap untouched
at 740 KiB and disk down 1 GB. Load spiked while ClamAV loaded signatures and
returned to baseline.

mailcow and MateMail were neither modified nor restarted; the P5 policy hook is
still on the live Postfix in its designed order, and both mail queues are empty.
No mail was sent and no customer data exists in the native store. Resources are not the constraint —
5.5 GiB RAM available, 4 GiB swap unused, 169 GB disk free, and ClamAV (the one
heavy service, ~1 GB) is memory-bounded.

---

# NE2 — Provisioning layer (COMPLETE, 2026-09-18)

The authoritative engine-side state behind the provisioning half of
`MailEngineAdapter`. Nothing here makes Postfix, Dovecot or Rspamd **consume**
that state — no lookup is wired and no mail is routed by any of it. That is NE3.

## The adapter contract did not change

All 26 abstract methods are unchanged. **NE2 implements 18 of them**:

```
domains          4    ensure_domain, set_domain_active, delete_domain, list_domains
mailboxes        6    ensure_mailbox, set_mailbox_active, set_mailbox_password,
                      set_mailbox_quota, delete_mailbox, list_mailboxes
last login       1    get_last_login
aliases          2    ensure_alias, delete_alias
forwarding       1    ensure_forwarding
DKIM             3    get_dkim_public_key, rotate_dkim_key, delete_dkim_key
health           1    check_health
                 --
                 18
```

**8 remain for NE3/NE4** — three rate-limit methods and `get_mailbox_usage`
(NE3), and four queue/quarantine methods (NE4). They raise
`EngineCapabilityMissing` naming the phase that owns them.

They are **not** stubbed to return empty results. An adapter that answered "the
queue is empty" without looking would report a healthy queue during an incident,
and one reporting 0 MB used would be wrong the moment NE3 delivers a message.
Refusing is the honest answer.

## Schema

`engine/native_api/migrations/`, applied by the API itself at startup — it is the
only component permitted to write this database, so it is the only one that can
safely change its shape. Django never connects to it.

```
domain             name, active, dkim_selector, the three storage numbers
mailbox            address, domain, password_hash, quota_mb, active, last_login_at
alias              address, active
alias_destination  destination + mailbox_id   <- the send-as marker
forwarding         mailbox_id, destination    <- deliberately NOT an alias
dkim_key           domain_name, selector, public_key, private_key_path
```

Quotas are **megabytes** throughout, named `_mb` so the unit cannot be misread.
This matches `MailboxSpec.quota_mb` exactly; reinterpreting it would silently
resize every customer mailbox.

Two constraints encode rules rather than restate them: a mailbox
`password_hash` must carry a scheme prefix, so an unhashed credential cannot be
written even by a caller that bypassed the API; and `public_key` may not contain
`PRIVATE KEY`.

`dkim_key.domain_name` is deliberately **not** a foreign key. The adapter
contract requires a signing key to outlive its domain, because
`remove_domain_from_engine` deletes the key explicitly and unconditionally
*before* the domain. A cascade would look tidier, break the contract test, and
delete the step that stops the next owner of a domain inheriting the previous
owner's key.

## Receiving is not sending

NE0 asked for the distinction; the schema makes it structural.

```
alias_destination.mailbox_id NOT NULL  ->  an internal mailbox
                                           THAT mailbox may send as the alias
alias_destination.mailbox_id NULL      ->  somewhere else entirely
                                           nobody gains any sending right
forwarding                             ->  a different table; the send-as query
                                           cannot reach it at all
```

This mirrors MateMail's own product rule exactly — `Alias.destination_mailbox`
(internal, confers send-as) versus `Alias.destination_address` (external, does
not) — without needing a new DTO field, because the engine can already tell
which destinations are mailboxes it hosts.

Keeping forwarding in its own table is the point. If it lived in
`alias_destination`, then a support address forwarding to an outside mailbox
would put that outside address one join away from the send-as query, and the
only thing preventing a spoofing right would be a `WHERE` clause someone has to
remember. A separate table makes the wrong answer unreachable rather than merely
unwritten.

`mailbox_id` is re-resolved whenever a mailbox is created or deleted, so whether
an alias confers send-as never depends on the order operations happened to
arrive in.

## Passwords

BLF-CRYPT (bcrypt) at cost 10, stored with Dovecot's scheme prefix. The
plaintext arrives in one request body, is hashed immediately, and is never
written to the database, to Redis, to a log line or to a response. Errors
describe the problem and never the value.

**The scheme was verified against the pinned image, not assumed.** `doveadm pw -l`
on dovecot/dovecot:2.4.1 lists BLF-CRYPT, and `doveadm pw -t` accepts a hash
produced by Python's bcrypt — succeeding for the right password, failing for a
wrong one. That check mattered: Dovecot emits one bcrypt variant prefix and
Python emits another, and only a measurement settles whether both verify.

Cost 10 rather than Dovecot's default of 5, which is low for a credential
guarding a mailbox — roughly 100 ms, expensive to crack offline and cheap enough
for IMAP clients that reauthenticate constantly.

## Why the API needed its own image

NE1 ran the API on stock `python:3.13-alpine` and said so proudly: standard
library only, no private registry needed to start. NE2 cannot keep that, and the
reason was measured.

Python 3.13 **removed** the `crypt` module (PEP 594). On the pinned base image
`import crypt` raises ModuleNotFoundError, `hashlib` offers no bcrypt, and
`bcrypt`, `passlib` and `cryptography` are all absent. A stock interpreter cannot
produce the hash the pinned Dovecot expects, and has no RSA generation for DKIM.

The alternatives were storing plaintext, weakening the scheme to fit the runtime,
or shelling into the Dovecot container to run `doveadm pw` — which would couple
provisioning to another container's lifetime and put the plaintext in an argv any
process listing can read. So: a minimal image under repository control with three
exactly-pinned dependencies, which is what NE0 Step 8 prescribes for this
situation.

**The uid is load-bearing.** `/var/lib/rspamd` is `drwxr-x--- 11333:11333` in
rspamd/rspamd:3.11. A DKIM key must be mode 0600 *and* readable by Rspamd at NE3,
which is only possible if the writer and reader share a uid. An earlier draft ran
as `nobody` and could not even list the directory — it would have failed the
first time DKIM was called, in production.

NE2 also **narrowed** the API's access: it used to mount all of `native_rspamd`,
giving it write access to Rspamd's bayes database to do a job that touches one
subdirectory. It now mounts a dedicated `native_dkim` volume at the same path
Rspamd interpolates, and Rspamd mounts that volume **read-only**.

## DKIM — crash-consistent by construction

RSA 2048 minimum, generated with the OS CSPRNG through `cryptography`, stored at
mode 0600 on the engine filesystem. The private key never enters the database, an
API response, or MateMail — `DkimKeyInfo` has no field it could travel in, and a
response guard refuses any payload carrying a forbidden key or PEM material.

### The invariant

```
after process death at ANY step, the domain has exactly ONE usable generation,
and the public key MateMail publishes is derived from the private key Rspamd
signs with

never: the database describing one generation while the live key is another
```

### How it is achieved: one source of truth

An earlier NE2 draft committed the database row and then moved the key into
place, and documented the resulting window instead of closing it. No ordering of
two commits closes it — a crash can always land between them. What closes it is
removing the second source of truth:

```
/var/lib/rspamd/dkim/<domain>.<selector>.key        the ACTIVE key (Rspamd reads this)
/var/lib/rspamd/dkim/<domain>.<selector>.g<tok>.key immutable generations
```

A generation file is written once and never modified. **Activation** makes the
active path a HARD LINK to one of them — a link into a temporary name followed by
`os.replace`, which is atomic — so "which generation is live" is an inode
identity rather than a claim someone recorded.

`get_dkim_public_key` then **derives** the public key from whatever is actually at
the active path. The row is metadata and a cache; when the two disagree the file
wins and the row is corrected. A reader therefore cannot receive a public key that
Rspamd is not signing with, because the answer is computed from the signing key
itself.

Hard links rather than a symlink: a symlink can dangle, and removing a generation
name must never remove the live key while the active path still references its
inode.

### The lifecycle

```
first creation     generate -> claim the row -> activate
rotation           generate -> ACTIVATE -> update the row
```

Different orders, and both are safe, because the row is never read directly:

| crash point | result |
|---|---|
| after generation, before the row is claimed | no row; old generation still live; orphan reconciled |
| after the row is claimed, before activation | row with no live key → reported as **no usable key**, never a stale one; reconciliation activates the generation the row names |
| after activation, before the row update | new generation live; the derived public key matches it immediately; the row self-heals |
| after the row update | steady state |

A failed row update that is an *exception* rather than a crash rolls the
activation back, so a failed call is a clean no-op. What "back" means depends on
what activation disturbed, and getting that wrong is how a stale key survives:

```
same selector      activation overwrote the live path -> re-activate the
                   previous generation under the same name
selector change    activation created a NEW path and left the old one alone ->
                   REMOVE the new path, or a live key survives under a selector
                   the database does not name
```

The rollback originally handled only the first case. On `mm1 -> mm2` it removed
the new generation file but left `<domain>.mm2.key` — a signing key that outlived
the call that returned an error.

**A row is not proof of a usable key.** First creation claims the row before it
activates, so a failure between them leaves a row, a generation and nothing live.
A retried `ensure_domain` therefore verifies the key is actually usable instead of
returning success on the row's existence; if it is not, it activates the
generation the row already names. It never mints a replacement and never chooses
between ambiguous generations — it fails honestly instead, because a fresh key
would publish material no DNS record matches.

### The lifecycle is serialised

Every DKIM mutation — creation, rotation, deletion and reconciliation — runs
under one PostgreSQL advisory lock. Two rotations of the same domain could
otherwise interleave:

```
A generates -> B generates -> A activates -> B activates
-> B writes the row and returns B -> A writes the row and returns A
```

A later read repairs the row, but the damage is already done: caller A was handed
public material that is not the signing key, and may already have published it in
DNS. That is not something a subsequent repair can undo, which is why this is a
lock rather than another reconciliation case.

**A database lock, not a Python one.** A `threading.Lock` protects one process.
The engine runs one API container today and is expected to run more; the moment
it does, a process-local lock protects nothing while looking like it does.

**Global rather than per domain.** Per-domain would be finer and would still be
wrong: reconciliation sweeps key files belonging to every domain, so it would
have to hold every domain's lock to be safe against an in-flight rotation
elsewhere. These operations are rare — a domain is created once and rotated
occasionally — so one lock is both simpler and sound. Mailbox, alias and
forwarding provisioning is unaffected.

**Session-scoped rather than transaction-scoped.** `pg_advisory_xact_lock` would
be tidier to release, but it would force every write in the lifecycle into one
transaction committing only at the end of the block — silently changing what a
crash leaves behind, which is exactly what the crash-consistency tests are
written against. Serialisation should not quietly rewrite the durability model it
is protecting.

The lock is released on every path:

```
success and handled exceptions   the `finally`
BaseException (test crashes)     the `finally`
process death / connection loss  PostgreSQL drops every session lock when the
                                 backend exits, so a killed container cannot
                                 wedge the next rotation
```

Acquisition uses a `lock_timeout` so an impossible wait **fails** instead of
hanging. A leaked lock that blocks forever is harder to diagnose than one that
says why it gave up, and a test that hangs is worse than one that fails.

Proven by racing real operations on separate connections: concurrent
same-selector rotations, concurrent different-selector rotations, rotation
against reconciliation, and rotation against deletion. The decisive check runs
*inside* each operation's own lock hold — every rotation confirms the public key
it is about to return is the key that is live. Verifying after the call returns
would prove nothing, because the next rotation may legitimately have replaced it
by then.

Three mutations confirm the tests depend on the lock: making it a no-op, swapping
it for a `threading.Lock`, and removing the release.

### Reconciliation

Runs at API startup, before the first request is served, and is also available as
an authenticated `POST /v1/dkim/reconcile`. It is deliberately **not** part of
`/health` or `/ready`: it can change database state, and a healthcheck that
mutates would make Docker's restart policy a provisioning trigger.

```
row with no live key        activate the generation the row names, if it exists
row disagreeing with live   correct the row from the live key
key files with no row       remove — nothing refers to them
interrupted temp files      remove
```

**It never generates a key.** Minting one during recovery would publish material
that no DNS record names, turning a recoverable inconsistency into mail that
silently fails DKIM. For the same reason it refuses to choose when several
generations survive with no live key and the row's pointer is also gone: which
one was published is not knowable from inside the engine, so it reports the
domain for an operator to rotate deliberately.

### Verified by failure injection

Crashes are simulated with a `BaseException`, so no `except Exception:` cleanup
runs — modelling a killed container rather than a handled error.

```
crash after generation, before the row is claimed
crash after the row is claimed, before activation
crash during same-selector rotation, before activation
crash during same-selector rotation, after activation
crash after the row commits
crash during a SELECTOR CHANGE, before the row update
crash during a SELECTOR CHANGE, after the row update but before cleanup
failed row update (exception) rolls the activation back — same selector
failed row update (exception) rolls the activation back — selector change
activation failure on first creation, then a retried ensure_domain recovers it
concurrent ensure_domain: the losers cannot delete the winner's key
concurrent RETRY after an activation failure converges on one identity
concurrent same-selector rotations, each returning the key it committed
concurrent different-selector rotations, leaving no stale active selector
rotation raced against reconciliation, and rotation raced against deletion
reconciliation against deliberately inconsistent synthetic state
```

After each, the tests assert the same property: the stored row, the derived
public key and the live private key all agree — or the engine honestly reports no
usable key. Twenty-one mutations of the design were each confirmed to make a test fail,
including reverting to the old ordering, trusting the cached row instead of
deriving, restoring the selector-blind rollback, treating the existence of a row
as proof of a usable key, making the lifecycle lock a no-op, swapping it for a
process-local `threading.Lock`, and removing its release.

## Validation and idempotency

Every endpoint validates before touching the database and **rejects unknown JSON
fields** rather than ignoring them: a silently dropped field turns a caller's
typo into a successful request that did not do what it said.

Internationalised domain names are **refused**, deliberately. MateMail's own
validator is ASCII-only so a U-label cannot arrive through the product path, and
transcoding with the standard library would mean IDNA 2003 — which disagrees with
IDNA 2008/UTS-46 on characters real registries use. The engine would store one
label while MateMail published DNS for another. Supporting IDNA is a real
decision requiring the `idna` package and a matching change in MateMail's
validator, made together.

Every create is an upsert, so two identical requests — a retry racing its
original is the normal case — are resolved by the database rather than producing
a unique violation for a caller that did nothing wrong.

## What was verified

```
1231 tests pass (was 1050 before NE2). The same adapter contract that runs against StubAdapter and
MailcowAdapter now runs against NativeMailEngineAdapter, over an in-process
transport that dispatches into the REAL request handlers against a REAL
PostgreSQL database running the REAL migrations.

Upgrade path: a database recreated at exactly the deployed NE1 state (version 1)
migrates forward to version 2 and gains the provisioning tables. Verified both in
the test suite and against the real API image and a real postgres container.

On the real image, on a real volume: key file mode 0600 owned by uid 11333,
DKIM responses carrying only the four public fields, a password absent from every
listing, auth refusing a wrong and a missing secret, unknown fields refused, and
forwarding leaving send-as unchanged.

DKIM crash consistency is proven by failure injection at every step of the
lifecycle, with crashes simulated as BaseException so no cleanup handler runs.
After each, the stored row, the derived public key and the live private key all
agree — or the engine honestly reports no usable key and reconciliation repairs
it.

Thirteen mutations of the security and crash-safety properties were each
confirmed to make a test fail: storing plaintext, unioning forwarding into
send-as, treating every alias destination as internal, dropping a key from the
response guard, cascading DKIM with the domain, letting the adapter send an empty
password, trusting the cached row instead of deriving from the live key,
reverting to the write-row-then-move ordering, disabling row-directed recovery,
minting a key during reconciliation, guessing between surviving generations,
making activation non-atomic, and pruning the generation the active key points at.
```

## NE2B — runtime validation on MateServer (2026-09-13)

```
release          e14e6221e192825bd53175e2afc6ac390649a455, CI green
api image        run 34765665185 (api only; the other four were unchanged since
                 the NE1 release and were correctly skipped)
api digest       ghcr.io/rizwansammo/matemail-native-api@sha256:
                 198c238c4c3f080cb74f23b0ae65b2b7ce10b38d9fe6fa8d9543cf26588aced6
schema           v1 -> v2, applied once, idempotent across an API restart
services         10 / 10 healthy throughout
```

Only `api` and `rspamd` were recreated — precisely the two services the release
changed. The other eight kept running, and no volume was removed at any point.

### What the runtime proved that the tests could not

The test suite runs against a scratch database and a temporary directory. These
are the properties that only the real deployment can show:

```
migration         the DEPLOYED v1 database upgraded to v2 in place, once, with
                  no duplicate version rows, and stayed v2 across a restart
uid               the API runs as 11333 and the FRESH native_dkim volume
                  inherited 0700 / 11333 from the image, as designed
key permissions   the live private key is mode 0600 owned by 11333 on the real
                  volume, and Rspamd can READ it while its mount refuses a write
                  ("Read-only file system")
hash              a password set through the API verified against the PINNED
                  Dovecot via `doveadm pw -t` — correct password PASS, wrong
                  password rejected
derivation        the API's public key matched the key derived from the live
                  private key, and the database row matched both
concurrency       4 concurrent rotations through the real API: all succeeded,
                  all distinct, exactly one live, no orphan generations, and
                  zero advisory locks left held
```

### Synthetic lifecycle

Everything ran under one timestamped `.invalid` namespace and was removed
afterwards. No mail was transmitted and no customer state was created.

```
domain       ensure x3 -> one row; active false/true round-trip
mailboxes    alice + bob; repeat ensure kept the credential; inactive mailbox
             still represented; last_login null (nothing has authenticated)
password     stored as {BLF-CRYPT}$2b$...; the plaintext appeared in NO text
             column of ANY table and in no API log line
quota        4096 MB round-tripped exactly; negative, zero and string rejected,
             and the stored value was unchanged by the rejections
alias        support -> alice: alias_destination.mailbox_id NOT NULL, so alice
             is an authorised sender and bob is not
external     press -> sink@example.invalid: mailbox_id IS NULL and the external
             destination authorises nobody
forwarding   alice -> external: stored in its own table, created no alias, and
             left alice's send-as set unchanged
dkim         first generation, then same-selector rotation, then mm1 -> ne2b2;
             after each, exactly one generation and no files under the old
             selector
```

### Cleanup

Deprovisioned through the supported lifecycle in the contract's order — aliases,
forwarding, mailboxes, deactivate, **DKIM explicitly**, then the domain, because
the engine deliberately lets a key outlive its domain and MateMail's
`remove_domain_from_engine` deletes it first for exactly that reason. Deletes
were repeated to confirm idempotency.

```
every provisioning table   0 rows
DKIM key store             empty (no keys, no generations, no staging files)
advisory locks held        0
reconciliation             reported nothing to repair
```

### Production

```
mailcow        20 containers, untouched and not restarted (StartedAt predates
               this session); queue empty; P5 policy hook still on the live
               Postfix; policy bridge healthy
MateMail       8 containers, untouched and not restarted
ports          mail ports 25/110/143/465/587/993/995 all bind 127.0.0.1 ONLY —
               zero bound to a non-loopback address. Host LISTEN count 40 before
               and 40 after. The only public listeners are 22, 80, 443 (and a
               :4000 belonging to another NetaMate application, unchanged)
UFW            rule checksum byte-identical
DNS / PTR / MX unchanged
native store   0 entries under native_vmail; 0 delivery log lines
```

### Resource impact

```
available RAM   4.5 GiB before -> 4.5 GiB after
swap            306 MiB before -> 306 MiB after (unchanged; the 306 MiB predates
                this work and is drift over 8 days of uptime)
disk            168 GB free, unchanged
native engine   851 MiB before -> 818 MiB after (ClamAV fluctuation dominates)
Native API      10.1 MiB -> 25.9 MiB, about +16 MiB for bcrypt, cryptography and
                psycopg. Acceptable and not worth optimising.
load            3.27 before -> 1.59 after
```

### Deployment-integrity fix, after the runtime validation

NE2B's functional validation was sound, but it exposed something the validation
itself could not: the API image carried **no application code**. `/opt/matemail`
was empty, and the container ran only because Compose bind-mounted
`engine/native_api` from the VPS over the path the ENTRYPOINT executes.

```
image digest  =>  bcrypt, cryptography, psycopg
image digest  =/=  the provisioning logic, the DKIM lifecycle, the migrations
```

A digest that does not cover a service's own code is not a pinned deployment; it
is a pinned runtime wrapped around whatever happens to be on the disk beneath it.
And the migrations were in the same position — the schema the API applies has to
be the schema that build was tested against.

The correction:

```
Dockerfile   COPY engine/native_api /opt/matemail/native_api   (source + migrations)
workflow     the api build alone uses the repository root as context, with
             `file:` naming the Dockerfile; the other four keep their own
             directory, since handing them the whole repository buys nothing
compose      no host path is mounted into the API at all; native_dkim stays,
             and the DKIM ownership model is unchanged
compose      NATIVE_API_IMAGE is `:?required` — the old `:ne2` default named a
             tag the workflow never publishes
```

Proven with the image alone, no bind mount: it starts, `/health` and `/ready`
answer, auth still refuses an unauthenticated `/ready`, the migration ladder is
present inside the image and took a v1 database to v2, the provisioning modules
import, bcrypt/cryptography/psycopg all work, and DKIM generation produced a
0600 key owned by 11333 whose derived public key matched.

Six mutations of the regression tests were each confirmed to fail: re-adding the
source mount, mounting any other relative or absolute host path, removing the
DKIM volume, restoring the unpublished `:ne2` fallback, and dropping the COPY.

**Deployed 2026-09-18** — see the section below.

### One repository-side finding

The Compose default for the API is `ghcr.io/rizwansammo/matemail-native-api:ne2`,
but the image workflow still publishes the moving tag `ne1` (hardcoded from the
NE1 phase). That tag therefore does not exist.

It did not affect that deployment — production pins the digest, and the digest
was verified against the commit-SHA tag. Corrected above by making
`NATIVE_API_IMAGE` required rather than defaulted, which removes the reference
instead of pointing it somewhere else. The workflow's moving tag stays `ne1`:
renaming it would strand every already-published image under a tag nothing points
at, and the moving tag is a convenience, not part of the deployment contract.

## NE2 final deployment — integrity fix live (2026-09-18)

```
release       be5b7a38641c7592675e532ad3ec7bd01b68b23b, CI run 34769877707 green
api image     Native Engine images run 35275580679 (api only; the other four
              were unchanged and correctly skipped)
api digest    ghcr.io/rizwansammo/matemail-native-api@sha256:
              5cacaea0555cadd242eb82dc81cf8f2cfb6fac85cf0619a7af7c1d8fd8276e1d
services      10 / 10 healthy; only `api` was recreated — the other nine kept
              their 4–5 day uptime
```

The digest was verified against the commit-SHA tag rather than trusted, and
confirmed to differ from the NE2B digest it replaces. All four other images stay
on their NE1 digests.

### The guarantee that was missing, and now holds

NE2B's API image carried only the dependency set: `/opt/matemail` was empty and
the container ran on a host bind mount of `engine/native_api`. The pin therefore
attested to bcrypt, cryptography and psycopg, and to nothing about the
provisioning logic, the DKIM lifecycle or the migrations actually executing.

Measured on the running container after this deployment:

```
Config.Image        the new digest
bind mounts         0   (the only mount is the native_dkim volume)
source in image     app.py, db.py, dkim.py, passwords.py, provisioning.py,
                    validation.py all present under /opt/matemail/native_api
migrations in image 001_foundation.sql and 002_provisioning.sql present
ENTRYPOINT          ["python3","-u","/opt/matemail/native_api/app.py"]
content match       all eight files sha256-identical to the pushed commit
host copy           still on disk at /opt/MateMailNative/engine/native_api and
                    NOT mounted by the container — it is now documentation, not
                    the code that runs
```

That last line is the point: the running code can be traced to a commit, not to
whatever happens to be on the VPS filesystem.

### Everything else unchanged

```
schema            v2, two schema_version rows, no migration re-applied on start
                  or on a subsequent restart
provisioning      all six tables exist and are empty (NE2B's synthetic data was
                  cleaned and nothing was recreated)
DKIM volume       native_dkim intact, dir 0700 owned by 11333, key store empty;
                  API writable, Rspamd read-only ("Read-only file system")
restart           API restarted alone: healthy, digest unchanged, still zero bind
                  mounts, native_dkim still mounted, schema still v2
isolation         no published host ports, all ten on matemail_native_engine
                  only, 0 on mailcow-network, no foreign mounts, none privileged,
                  no added capabilities
mail ports        25/110/143/465/587/993/995 all bind 127.0.0.1 only; zero on a
                  non-loopback address. Public listeners remain 22, 80, 443 plus
                  a :4000 belonging to another NetaMate application
production        mailcow 20 and MateMail 8 containers, untouched and not
                  restarted (StartedAt predates this session); P5 policy hook
                  intact; both queues empty; UFW byte-identical; DNS/PTR/MX
                  unchanged; no mail sent; no customer data
```

### Two operational notes

The MateServer GHCR credential had expired since NE2B, so `docker compose pull`
returned `unauthorized`. It was refreshed from the authenticated `gh` CLI without
the token passing through a terminal, an argument or a file. The old container
kept running throughout — a failed pull recreates nothing.

Host resources drifted over the five days between NE2B and this deployment
(available RAM 4.5 → 3.2 GiB, swap 306 MiB → 1.4 GiB, disk 168 → 154 GB free),
driven by other NetaMate applications on the box; the host LISTEN count rose from
40 to 46 for the same reason, all docker-proxy entries and none mail-related. The
Native Engine's own footprint is unchanged at roughly 860 MiB, of which ClamAV is
645 MiB.

**NE2 COMPLETE.** mailcow remains the production engine and
`MAIL_ENGINE_ADAPTER` is still "mailcow": NE2 gave the Native Engine
authoritative provisioning state, not the mail path. Nothing in Postfix, Dovecot
or Rspamd consumes that state — that is NE3.

---

## NE3 — Postfix + Dovecot + Rspamd mail flow

**Status: COMPLETE (2026-09-18).** Implemented, validated locally, and validated
at runtime on MateServer. mailcow remains the production engine and
`MAIL_ENGINE_ADAPTER` is still `mailcow` — NE3 gave the Native Engine a working
mail path, not the production traffic. That switch is NE5's.

NE3 is where the Native Engine stops being a provisioning database and becomes a
mail server. Every claim below was measured against a running isolated stack on
`.invalid` domains.

### The path a message takes

```
authenticated client
  -> Postfix submission (587, STARTTLS required)
     SASL delegated to Dovecot over a unix socket on a shared volume
  -> smtpd_sender_login_maps + reject_authenticated_sender_login_mismatch
     (may this login use this envelope sender?)
  -> Rspamd milter: ClamAV, oletools, spam scoring, DKIM signing
  -> Postfix virtual transport
  -> Dovecot LMTP (port 24)
  -> Maildir at /var/vmail/<domain>/<local part>, owned 5000:5000
```

Proven end to end: a message from `alice@` to `bob@` arrived in the recipient's
Maildir carrying `DKIM-Signature: ... d=<domain>; s=mm1`, delivered
`with ESMTPSA` and then `with LMTP`.

### What each component may read

Migration 003 publishes six views and revokes NE2's table grants. Postfix and
Dovecot hold separate PostgreSQL credentials, and neither can read the other's
views, reach a base table, write anything, or create anything.

The one view that matters most is `postfix_sender_login`. It answers "who may
send as this address", and it is built so the wrong answers are unreachable
rather than filtered: it never references the `forwarding` table, and an
external alias destination is excluded by an INNER JOIN on a NULL `mailbox_id`.

### Configuration findings worth keeping

Several behaviours could only be established by running the software:

* **Dovecot cannot expand `%{env:...}`.** It parses, `doveconf -n` echoes it
  back, and libpq receives an empty string. Both images now render secrets
  through entrypoints.
* **Dovecot 2.4 renamed userdb fields to setting names.** `home`, `uid` and
  `gid` no longer exist; the view returns `mail_home` and `quota_storage_size`.
* **A quota default inside the `quota storage` block overrides the userdb.**
  Written there, every mailbox silently receives the fallback. It belongs at the
  top level, with the quota root left empty.
* **Rspamd's `CLAM_VIRUS` scores 0.00 by default** — it detects and delivers.
  See `docs/SECURITY.md` § Native Engine NE3.
* **Dovecot POSTs auth-policy reports to the configured URL verbatim**, without
  appending `allow`/`report`.
* **The upstream Dovecot image has no coreutils** — no `cat`, `chown`, `chmod`
  or `tr` — so its entrypoint uses shell builtins only.

### Isolation held throughout

No published host ports, `.invalid` domains only, the Postfix queue empty at the
end, zero outbound SMTP delivery attempts, and every `relay=` in the logs
pointing at Dovecot. No Internet mail was sent.

### DKIM verified cryptographically, not by inspection

A message produced by the real Postfix -> Rspamd -> LMTP flow was taken byte for
byte out of the recipient's Maildir and verified with `dkimpy` against the public
material `GET /v1/dkim` returns for that domain. The key reached the verifier
through a DNS callback, because `.invalid` cannot publish records and public DNS
was not touched.

```
original   s=mm1    body hash VERIFIES, header signature VERIFIES -> PASS
rotated    s=alt7   body hash VERIFIES, header signature VERIFIES -> PASS
rotated message against the OLD key                              -> FAIL
original message against the OLD key                             -> PASS
any message against an unrelated RSA key                         -> FAIL
```

The negative controls matter as much as the positives: with the wrong key the
BODY HASH still verifies and only the HEADER SIGNATURE fails, which is exactly
RFC 6376 behaviour and shows the verifier is discriminating on the key rather
than returning a blanket success.

### Selector rotation never creates an unsigned window

A selector change RETIRES the outgoing key instead of deleting it.

```
rotate mm1 -> alt7
    write <domain>.mm1.key.retired          (marker first, before anything else)
    activate <domain>.alt7.key              (the commit point)
    update the row, republish selectors.map
    prune mm1's GENERATIONS
    keep <domain>.mm1.key                   <- the part that matters
```

The old key keeps its exact filename, because that is the name a stale Rspamd
worker opens. During the grace period both outcomes are correct signatures and
neither is unsigned:

```
stale worker -> mm1 + retained mm1 key -> verifies against the old DNS record
fresh worker -> alt7 + new key         -> verifies against the new DNS record
```

Proven cryptographically: a message signed with the RETAINED key verified
against the public material the API published before the rotation.

**Retirement grace: 1800 seconds**, overridable through
`NATIVE_DKIM_RETIREMENT_GRACE` (tests use a short value rather than sleeping).

**How long propagation actually takes.** `map_watch_interval` is 300s, but that
is not the number that governs a file map — `map_file_watch_multiplier` is 0.1,
so a local file map refreshes at **30s** at the shipped defaults. Measured with
`rspamadm configdump`, after an earlier draft of this document quoted 300s and
was wrong. The 1800s grace is 60x the real bound, which is the margin the
invariant deserves.

Rspamd needs **no restart, no `rspamadm reload` and no configuration edit**: its
container `StartedAt` was unchanged across rotations, and its log shows
`rereading map file .../selectors.map`. Even with `map_watch_interval` forced to
3600s for a test, the rename was picked up promptly — the refresh is driven by
the file's mtime, which the atomic replace changes.

The retirement model does not depend on any of that timing. That is the point:
the previous design was correct only because refresh happened to be fast.

**Cleanup is reconciliation's.** No scheduler was added. Reconciliation runs at
API startup and through `/v1/dkim/reconcile`, and it distinguishes three things
that all look alike on disk:

```
retired, inside grace   KEEP           (reported as retired_retained)
retired, grace expired  remove + marker (retired_removed)
no marker at all        orphan, removed (orphans_removed)
```

A marker found on the selector the database still names is cleared — that is the
crash window where the marker was written and the rotation then failed, and left
alone it would eventually delete the live key. A marker with no key beside it is
removed as litter. Deleting a domain's DKIM removes its retired material too.

### Same-selector rotation is a different thing

`mm1 -> mm1` with a new key retires nothing, and should not: the filename never
changes, so no worker can be looking for a name that vanished, and `activate`
uses an atomic rename so a reader gets either the old inode or the new one and
never a torn file.

**DNS is the real exposure here, and filesystem consistency cannot fix it.**
Receivers that cached the old `p=` value will reject signatures from the new key
until the record's TTL expires. A same-selector rotation is therefore NOT
production-safe merely because the local key store is consistent — it needs a
DNS-side plan (publish the new key, wait out the TTL, then rotate), and a
selector CHANGE is the safer operation precisely because old and new records can
coexist. This is a production rotation concern owned by NE7, not something NE3
resolves.

### MateServer runtime validation (2026-09-18)

Deployed from `64b19471c39e51c0cfc8efe35a0b777ac3c2ba51`, digest-pinned, no Git
checkout and no build on the VPS.

```
api      sha256:abfa87acb7751ec6daf03842b1a9bedd120e8eb276673d725b5a8b195fb8be14
dovecot  sha256:d90ce8e51e5c6c169736c0d95ecc25dcaccac7d20e0bb1d8bded9b3f903f94c5
postfix  sha256:1f00551f026be80ce61d770f3c061db3a3a035901bc71259ac04fa649742e1eb
```

unbound and olefy were unchanged by NE3 and stay on their NE1 digests.

Results: 10/10 healthy, schema v3 with migration 003 applied exactly once,
`/health` 200, `/ready` 200 authenticated and 403 unauthenticated, 18/18 reader
isolation checks, end-to-end mail delivered to Maildir owned 5000:5000, DKIM
verified cryptographically (body hash and header signature) against the API's
published material with an unrelated key correctly failing, selector rotation
retaining the old key `0600 11333:11333` beside its `.retired` marker, EICAR
rejected `554` with `CLAM_VIRUS(2000.00)`, quota refused at LMTP with
`Quota exceeded`, and `last_login` stamped only on successful authentication.

Production was untouched throughout: mailcow's 20 containers and MateMail's 8
were not restarted (their `StartedAt` predates the session), the P5 policy hook
and bridge are intact, the UFW checksum is byte-identical to the pre-deployment
baseline, DNS/PTR/MX are unchanged, and Postfix recorded **zero** outbound SMTP
deliveries with no relay target other than Dovecot.

Two operational notes worth carrying forward:

* **Rspamd is not recreated by a config-only change.** Its `local.d` is a bind
  mount, so Compose sees no change to its image or environment and leaves the
  container running with the configuration it started with. NE3's new
  `groups.conf` only took effect after an explicit `docker compose restart
  rspamd`. Any future change to Rspamd's configuration needs that restart, and a
  deployment that forgets it will look successful while the old rules are live.
* **Deleting a mailbox does not delete its Maildir.** The provisioning rows go
  and the mail stays. That is the safer default — destroying customer mail
  should be an explicit operation — but the adapter contract does not yet say
  who owns that deletion, and NE4 should decide rather than leave it implicit.

### What NE3 does not include, and why

NE3 built the mail path. Four adapter methods remain unimplemented and they are
**NE4's**, not unfinished NE3 work — nothing in the delivery path calls them:

* `get_mailbox_usage` — quota ENFORCEMENT already works (Dovecot refuses an
  over-quota delivery with `Quota exceeded`). Reading usage back needs an
  administrative channel to Dovecot that NE4's quota validation needs anyway.
* the three rate-limit methods — NE4 lists rate limiting among the behaviours it
  validates in isolation; the mechanism belongs with Rspamd's ratelimit module
  and the policy path.

They refuse with `EngineCapabilityMissing`, which is the honest answer. A
fabricated `used_mb=0` would be wrong the moment the first message is delivered,
and wrong quietly.

NE3 owes nothing further. The rebuilt Dovecot, Postfix and API images are
published and pinned, and MateServer runtime validation is complete.

Adapter methods remain **18 of 26 implemented, 8 capability-missing** — all
eight now attributed to NE4. NE3 added no adapter methods, only the mail path
underneath them.
