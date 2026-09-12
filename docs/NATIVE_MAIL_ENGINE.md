# NATIVE_MAIL_ENGINE.md — MateMail Native Engine Migration (NE0–NE8)

**Status:** planned. **Nothing is implemented. Nothing is started.**
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
NE0  not started        NE5  not started
NE1  not started        NE6  not started
NE2  not started        NE7  not started
NE3  not started        NE8  not started
NE4  not started

Native engine implementation:  none
mailcow:                       live, production, unmodified
```

A DEC recording the native-engine decision should be raised when NE0 completes
and the architecture is actually chosen — not now. Writing it before the
analysis would record a decision nobody has made yet.
