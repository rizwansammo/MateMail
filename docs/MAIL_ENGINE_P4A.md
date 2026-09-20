# Mail Engine — P4A Architecture and Integration Design

**Product:** MateMail
**Phase:** P4A (design + adapter integration). **Nothing in this document has been installed.**
**Date:** 2026-09-11
**Relates to:** DEC-001 (mailcow), DEC-007r (DKIM ownership), DEC-011 (integrated product), DEC-012 (launch sequencing), DEC-013 (first-party transactional mail)

> This supersedes the API and DKIM sections of `MAIL_ENGINE.md`, which were
> written from memory in an earlier phase. Every fact below was verified against
> the pinned release's own specification and source. Four defects in the earlier
> adapter were found that way and are called out explicitly in §5 — all four
> would have failed only against a real engine.

---

## 1. Version pin

**`2026-07b`** — published 2026-08-18, GitHub's own `latest`, `prerelease: false`.

Verified with `gh api repos/mailcow/mailcow-dockerized/releases`, not from a
blog post or memory.

**`latest` is not an installation strategy.** The engine holds customer mail;
an unattended change of Postfix, Dovecot or Rspamd is an outage nobody chose
the timing of. The tag is pinned in configuration, upgrades are deliberate, and
the adapter records which release its endpoint mapping was verified against
(`VERIFIED_AGAINST_ENGINE_VERSION` in `mailcow_adapter.py`) so an upgrade has an
obvious re-verification step.

### Sources used

| Source | Used for |
|---|---|
| `repos/mailcow/mailcow-dockerized/releases` (GitHub API) | Release tag, date, prerelease flag |
| `data/web/api/openapi.yaml` @ `2026-07b` | Authoritative endpoint list (96 paths), auth header, parameter shapes |
| `data/web/inc/functions.dkim.inc.php` @ `2026-07b` | What the DKIM read actually returns, including `privkey` |
| `generate_config.sh` @ `2026-07b` | Every `mailcow.conf` variable, port bindings, SKIP_* flags |
| `docs.mailcow.email` — Advanced SSL, reverse proxy | External certificate mechanism |

---

## 2. Engine topology

### Installation directory — `/opt/mailcow-dockerized`

Upstream's own default, and the path every mailcow script, doc and support
answer assumes. Deviating buys nothing and costs us the ability to follow
upstream instructions literally during an incident. It sits beside
`/opt/MateMail` rather than inside it: they are separate products with separate
lifecycles, and a MateMail deployment must never be able to touch engine state.

### Docker networks

Mailcow brings its own bridge network (`mailcowdockerized_mailcow-network`) and
manages it. **We do not join MateMail's containers to it, and we do not join
mailcow to `matemail_internal`.** The two stacks communicate over exactly two
paths, both narrow:

1. **MateMail → engine API**, to the engine's HTTP port.
2. **MateMail → engine SMTP submission**, for platform transactional mail.

Both crossings leave a container and reach the host, so neither can use
`127.0.0.1`. The address and mechanism are worked out in §8 and must be
measured on MateServer in P4B before becoming configuration.

Everything else stays inside its own stack. `matemail_internal` keeps
`internal: true` — Postgres and Redis remain unreachable from the engine, and
the engine's database is equally unreachable from MateMail.

### Host ports

`HTTP_BIND` takes an IP and `HTTP_PORT` a port — mailcow's generator is
explicit that they are separate and must not be combined as `IP:PORT`.

| Setting | Value | Why |
|---|---|---|
| `HTTP_BIND` | `127.0.0.1` | Loopback only. Host nginx is the sole public path. |
| `HTTP_PORT` | `8025` | **Verified free** with `ss -tulpn`. Follows the existing `80xx` convention (8001, 8005, 8010, 8015, 8016, 8020 are taken). |
| `HTTPS_BIND` | `127.0.0.1` | |
| `HTTPS_PORT` | `8453` | **Verified free.** Only needed if we terminate TLS twice; see §4. |

**8081, 9081, 9082 and 65510 are excluded.** mailcow's own
`generate_config.sh` says *"IMPORTANT: Do not use port 8081, 9081, 9082 or
65510!"*, and independently, **8081 is already in use on MateServer** by
another application's `docker-proxy` — confirmed by `ss`, not assumed.

`network_mode: host` is not used anywhere.

### Configuration mechanism

`mailcow.conf` for everything it exposes, plus `docker-compose.override.yml`
for anything it does not. Upstream's `docker-compose.yml` is never edited —
that file is replaced wholesale by `update.sh` on every upgrade, so a local
edit is silently reverted at the worst possible moment. The override file is
the supported extension point and survives upgrades.

---

## 3. Firewall design

**No firewall change is made in P4A.** This is the design to apply in P4B.

Current state, verified read-only: UFW active, allowing only 22/tcp, 80/tcp,
443/tcp (v4 and v6). No mail port is listening.

### The actual problem

Docker publishes ports by writing `DNAT` rules into `nat/PREROUTING` and
filtering in the `DOCKER` chain. That path is traversed **before** UFW's rules
in `filter/INPUT`, so a container publishing `0.0.0.0:25` is reachable from the
internet even though UFW never allowed port 25. The common advice — disable
UFW, or set `iptables: false` in the Docker daemon — trades one problem for a
worse one.

### The approach

Keep UFW exactly as it is, and control container-published ports in
`DOCKER-USER`, which Docker guarantees is traversed first and never flushes on
daemon restart:

1. Mail ports are published **deliberately** and only the ones the product
   needs (below).
2. `DOCKER-USER` carries an explicit allow/deny set for those ports, so the
   rule that governs mail exposure lives in one readable place rather than
   being an emergent property of Docker's chain ordering.
3. UFW continues to govern host-native services (SSH, nginx) unchanged.
4. Admin-only ports (`DOVEADM_PORT`, `SQL_PORT`, `REDIS_PORT`) keep upstream's
   loopback bindings — `127.0.0.1:19991`, `127.0.0.1:13306`, `127.0.0.1:7654`
   — so they are never published at all.

### Ports the product actually needs

| Port | Verdict | Reasoning |
|---|---|---|
| **25** | **Required** | Server-to-server delivery. Without it MateMail cannot receive mail, which is the product. |
| **587** | **Required** | Submission with STARTTLS. The modern standard (RFC 6409) and what every current client tries first. |
| **465** | **Offer as well** | Implicit TLS submission (RFC 8314 now recommends it). Some clients, and some networks that block 587, only work here. Offering both costs one listener and removes a class of "my mail client won't connect" support load. |
| **993** | **Required** | IMAPS. The only mailbox access protocol we commit to at launch. |
| 143 | **Not exposed** | Cleartext IMAP with optional STARTTLS. 993 covers every client we support; leaving 143 closed removes a downgrade path. |
| 110 / 995 | **Not exposed** | POP3. No product requirement. Mail that customers can delete off the server by accident is a support burden we are not choosing. |
| 4190 | **Not exposed** | ManageSieve. Server-side filters are not a launch feature; P8's webmail may revisit it. |

**On 465 vs 587:** offer **both**. 587 is the standard and the default;
465 exists because a meaningful number of residential and corporate networks
block 587 outbound, and because iOS/macOS Mail defaults to implicit TLS. They
are the same submission service behind two listeners, both authenticated, both
TLS — so this is not an extra attack surface in any meaningful sense, it is one
service reachable two ways. Documenting 587 as the recommended setting and 465
as the fallback gets the best of both.

---

## 4. Canonical hostname and TLS

**Canonical engine hostname: `mx.matemail.online`.** Already pointed at
MateServer. **No DNS is changed in P4A.**

| Element | Design |
|---|---|
| `MAILCOW_HOSTNAME` | `mx.matemail.online` — becomes the SMTP HELO/EHLO name |
| HELO/EHLO | `mx.matemail.online`, matching forward and reverse DNS |
| PTR | Must resolve `<MateServer IP>` → `mx.matemail.online`. Set at Contabo, not in DNS zone files. **Not done; P4B.** |
| Certificate SAN | `mx.matemail.online` at minimum; `autodiscover`/`autoconfig` names only if we implement those services |

### Certificate flow

Host-native Certbot remains the single TLS authority on this box — it already
owns `portal.matemail.online`, `matemail.online` and `www.matemail.online`, and
two ACME clients competing for port 80 is a renewal failure waiting to happen.

Therefore: **`SKIP_LETS_ENCRYPT=y`**. mailcow's own ACME container is disabled
and never binds port 80.

The supported external-certificate mechanism, per mailcow's Advanced SSL
documentation:

1. Certbot obtains/renews `mx.matemail.online` using the existing host nginx
   webroot.
2. A deploy hook **copies** (never symlinks — mailcow's docs are explicit, and
   the containers cannot follow a host symlink into `/etc/letsencrypt`):
   - `fullchain.pem` → `/opt/mailcow-dockerized/data/assets/ssl/cert.pem`
   - `privkey.pem` → `/opt/mailcow-dockerized/data/assets/ssl/key.pem`
3. The hook restarts the three containers that hold the cert open:
   `postfix-mailcow`, `dovecot-mailcow`, `nginx-mailcow`.

Renewal is therefore automatic and identical to renewal, because the hook runs
on every successful renewal rather than being a one-off install step. The hook
must be idempotent and must not fail the certbot run if a container is already
stopped.

---

## 5. Adapter — API mapping

Every path below was checked against `openapi.yaml` at `2026-07b`. Auth is the
`X-API-Key` header, which the existing adapter already sends correctly.

| Capability | Endpoint | Notes |
|---|---|---|
| health | `GET /api/v1/get/status/containers` | |
| domain create/reconcile | `POST /api/v1/add/domain`, `POST /api/v1/edit/domain` | |
| domain suspend/activate | `POST /api/v1/edit/domain` (`active`) | Assignment, idempotent |
| domain delete | `POST /api/v1/delete/domain` | |
| domain list | `GET /api/v1/get/domain/all` | `all` is the documented `{id}` value |
| mailbox create/reconcile | `POST /api/v1/add/mailbox`, `POST /api/v1/edit/mailbox` | |
| mailbox password / quota / active | `POST /api/v1/edit/mailbox` | |
| mailbox delete | `POST /api/v1/delete/mailbox` | |
| mailbox list | `GET /api/v1/get/mailbox/all`, `GET /api/v1/get/mailbox/all/{domain}` | |
| alias create/update/delete | `POST /api/v1/add/alias`, `/edit/alias`, `/delete/alias` | |
| DKIM generate | `POST /api/v1/add/dkim` — `{dkim_selector, domains, key_size}` | **The engine generates the pair** |
| DKIM read (public) | `GET /api/v1/get/dkim/{domain}` | Returns `privkey` — see §6 |
| DKIM duplicate | `POST /api/v1/add/dkim_duplicate` | Not used |
| DKIM delete | `POST /api/v1/delete/dkim` | |
| queue read / cancel | `GET /api/v1/get/mailq/all`, `POST /api/v1/delete/mailq` | |
| quarantine read | `GET /api/v1/get/quarantine/all` | |
| quarantine **release** | **does not exist** | See below |

### Two corrections to the P1 adapter, both implemented

**1. `release_quarantine_item` called an endpoint that does not exist.** It
POSTed to `/api/v1/set/quarantine/release`. mailcow 2026-07b defines only
`get/quarantine/all` and `edit/quarantine_notification`. In production the call
would have 404'd, been classified `NotFound`, and been swallowed by a handler
that treats `NotFound` as "already actioned" — an operator would have seen
success while the message stayed quarantined. It now raises a new typed
`EngineCapabilityMissing`, and the **stub raises identically** so a feature
cannot be built against a capability the engine lacks. Releasing quarantined
mail is P5 work and needs either a newer engine API or a direct Rspamd path,
decided then.

**2. The DKIM read can return private key material.** Covered in §6.

**3. Every delete used the wrong HTTP method.** `delete_domain`,
`delete_mailbox` and `delete_alias` sent `DELETE`. `json_api.php` at `2026-07b`
rejects anything but `POST` on the delete branch with
`405 "only POST method is allowed"`. All three operations would have failed
against a real engine. `DELETE` is the RESTful choice and the wrong one here.

**4. `cancel_queue_message` sent an unmatchable payload.** It posted
`{"id": [qid]}`. The router assigns the whole request body to `$_POST['items']`
and calls `mailq('delete', array('qid' => $items))`, so the body must be a
**bare array** of queue ids. The dict arrived nested under `qid` and could
never match a queue entry. Found by reading the router and
`functions.mailq.inc.php`, not the OpenAPI example — the spec's example for
that path documents only the `super_delete` flush variant.

None of these four change MateMail's product semantics. They make the intended
semantics actually happen, which is why they were fixed rather than escalated.
All four are now pinned by `RequestContractTest`, which asserts the exact
method, path and body of each call.

### Request/response contract audit

Every adapter operation was checked against the pinned spec, and against
`json_api.php` where the spec was ambiguous. Confirmed correct and unchanged:

| Shape | Verified |
|---|---|
| `add/*` | Flat JSON object; the router assigns the whole body to `$_POST['attr']` |
| `edit/*` | `{"attr": {...}, "items": [...]}`; the router re-encodes both keys separately |
| `delete/*` | Bare JSON array; the router assigns the whole body to `$_POST['items']` |
| `edit/alias` items | The alias **id**, not the address — the adapter already resolved this correctly |
| `delete/dkim` | Bare array, routed as `array('domains' => $items)` |
| Auth | `X-API-Key` header |
| Success body | `{type, log, msg}`; application-level failure arrives inside a 200, which `_raise_for_body` already inspects |

A false alarm worth recording: an automated diff flagged `get/domain/all` and
`get/mailbox/all` as missing, because the spec parameterises them as `{id}`.
The spec's own example value for `{id}` is `all`, so both are correct and were
left alone. Checking before "fixing" mattered here.

---

## 6. DEC-007r — DKIM migration

### What the engine does

`POST /api/v1/add/dkim` generates the keypair **inside the engine** and stores
the private half in the engine's Redis. That is exactly the end state DEC-007r
requires: MateMail asks, the engine generates and keeps.

### The hazard

Reading `functions.dkim.inc.php` at `2026-07b`: the `details` branch always
sets a `privkey` field. It is `''` normally — but if an operator enables
`$SHOW_DKIM_PRIV_KEYS`, the API returns the real base64-encoded private key.

> **Correction (P4B).** This document originally described
> `SHOW_DKIM_PRIV_KEYS` as a `mailcow.conf` variable set to `n`. That was
> wrong, and setting it there would have had no effect whatsoever — the
> preflight would have "passed" against a setting the engine never reads.
>
> It is a **PHP variable**, not an environment variable:
>
> | | |
> |---|---|
> | Upstream default | `data/web/inc/vars.inc.php` — ships as `false` |
> | Persistent override | `data/web/inc/vars.local.inc.php` |
> | Required effective value | `$SHOW_DKIM_PRIV_KEYS = false;` |
>
> `vars.local.inc.php` is the file to write, because `vars.inc.php` is
> overwritten on upgrade. The value that matters is the **effective** one in
> the running php-fpm container, which is what P4B verified rather than
> inferring it from either file.

### The guarantee, stated precisely

Stripping a private key *after* receiving it is defence in depth, not proof
that the key never left the engine. If the API returns it, it has already
crossed into the MateMail process, its memory and potentially its logs. The
guarantee therefore has three layers, and only the first is a real boundary:

| Layer | Control | What it actually guarantees |
|---|---|---|
| **1 — primary** | The engine is configured never to expose private keys: **`$SHOW_DKIM_PRIV_KEYS = false;`** in `data/web/inc/vars.local.inc.php` | The key never crosses the engine boundary at all |
| **2 — defence in depth** | The adapter discards `privkey`, `private_key`, `priv_key`, `key` unconditionally on every read and logs the misconfiguration without printing the key | If layer 1 is wrong, the material is dropped at the edge of MateMail rather than propagating |
| **3 — structural** | `DkimKeyInfo` has no field capable of holding private material; no port method reads or writes one; no serializer or API response exposes one | Even a future coding error has nowhere to put it |

**P4B preflight requirement (satisfied):** before the real adapter is enabled,
verify the **effective** value of `$SHOW_DKIM_PRIV_KEYS` inside the running
php-fpm container — not the contents of a config file, which may not be the
file the engine reads. If it is enabled, that is an **engine security
misconfiguration** — do not proceed with production activation until it is
corrected. This is a go/no-go check, not a warning.

P4B ran this check against the live engine: the effective value is `false`, and
the DKIM read returns `privkey` as an empty string. Layer 1 holds.

Layer 2 stays regardless. It is cheap, it is tested, and it converts a silent
configuration mistake into a loud log line.

### Migration stages

| Stage | When | What | Reversible |
|---|---|---|---|
| **1 — implemented in P4A** | now | Django stops generating keypairs. `DomainListCreateView` no longer calls `generate_dkim_keypair`; both DKIM fields start empty. Provisioning adopts the engine's key. Existing rows untouched, column retained. | Yes — revert the commit |
| **2 — P4B** | after the engine is live and verified | For each existing domain: have the engine generate, adopt the public material, then blank `dkim_private_key` row by row. Explicit, observable, resumable. | Yes — the column still exists |
| **3 — later P4** | after stage 2 completes for every row | Drop `Domain.dkim_private_key`, delete `apps/domains/dkim.py`, retire `DKIM_ENCRYPTION_KEY`. | No — deliberately last |

**Stage 1's safety property**, and the reason it is not a one-liner:
`rotate_dkim_key` is **not idempotent** — every call mints a new pair and
invalidates the DNS record the customer has already published. Provisioning is
a retryable Celery task. So `_adopt_engine_dkim` **reads first and generates
only on absence**, never replacing an existing key. Asserted directly by
`test_repeated_provisioning_never_rotates_a_published_key`.

**DKIM adoption fails closed.** An earlier draft of this design treated a
missing DKIM key as tolerable — "the domain is usable unsigned". That was
wrong, and is corrected here. A domain marked `mail_engine_provisioned=True`
without DKIM is advertised to the customer as ready while being unable to sign
a single message, and unsigned mail from a brand-new domain is how a sending
reputation is destroyed before it exists.

Successful adoption is therefore part of successful provisioning:

```
ensure engine domain
  → read existing DKIM key
  → if absent, generate once (never on retry with a key present)
  → require usable public material
  → persist public material
  → only then mail_engine_provisioned = True
```

A transient engine failure raises through the task's existing retry path; an
explicit rejection is terminal and recorded for the customer, exactly as for
`ensure_domain`. An engine that answers without usable material counts as a
failure, not a success.

No auto-verification and no silent replacement anywhere in this path.

---

## 7. Customer domain provisioning flow

Unchanged from P3a in its gating, extended at the DKIM step:

```
customer adds domain
   → ownership TXT verified          (P3a — hard gate, four layers)
   → MateMail creates engine domain  (ensure_domain, idempotent)
   → engine generates DKIM           (only if absent — §6)
   → MateMail displays required DNS  (public material only)
   → DNS health verifies MX/SPF/DKIM/DMARC
   → mailbox creation becomes usable
```

The ownership gate is unchanged and remains the hard precondition: an
unverified domain cannot reach the engine, refused at the view, the task
boundary, and both mailbox paths. **A platform admin cannot bypass it** — the
refusal is in `assert_provisionable`, not in a permission class, so there is no
role that routes around it.

Tenant isolation, plan quotas, audit logging, idempotency and the
`select_for_update` concurrency protection from P3b all apply unchanged.

---

## 8. Platform transactional email (DEC-013)

Sending identity: **`MateMail <noreply@mail.matemail.online>`**, already
configured in production.

### Design

`mail.matemail.online` is provisioned in the engine as a **normal domain with a
normal authenticated mailbox** — not a relay exception. It gets its own:

- mailbox/service credential, distinct from any customer credential
- rate limit, so a runaway platform loop cannot consume customer sending budget
- DKIM key, separate from every customer domain
- logs, distinguishable by sender domain
- reputation, isolated from customer mail

**No relay bypass.** The temptation is to let MateMail's own mail skip
authentication because "it's us". That would create exactly one unauthenticated
submission path into the engine, which is the definition of the thing we
promise not to have. Platform mail authenticates like everyone else.

### The internal hostname problem

`apps/accounts/mailer.py` refuses `localhost`, `127.0.0.1` and `::1` as
`EMAIL_HOST`. That guard is deliberate (it caught a silently-discarding
production config in P3c) and must not be weakened. But the engine will be on
the same host, and TLS certificate validation must pass against the name we
connect to.

**Proposal: connect to `mx.matemail.online` and resolve it internally.**

- The certificate already has to cover `mx.matemail.online` for SMTP anyway, so
  hostname validation passes with no extra certificate and no exception.
- A host-level `/etc/hosts` entry (or a Docker `extra_hosts` entry on the
  MateMail backend, worker and beat services) maps `mx.matemail.online` to the
  loopback/bridge address of the engine's submission listener. Traffic never
  leaves the host and never hairpins through the public internet.
- The loopback guard is satisfied honestly: the configured value is a real
  public hostname, not a loopback literal. We are not tricking the check — the
  name genuinely is the engine's canonical identity.
- Certificate validation stays **on**. No `EMAIL_USE_TLS=False`, no custom SSL
  context, no verification disabled.

### Both container-to-host paths (P4B must resolve each)

The same problem appears twice, and the first instance was nearly missed. **The
MateMail backend is a container; `127.0.0.1` inside it is the container, not
MateServer.** Two crossings need a real address:

| Path | Configured as | Naive value that is wrong |
|---|---|---|
| MateMail backend/worker → **engine API** | `MAIL_ENGINE_API_URL` | `http://127.0.0.1:8025` — resolves to the MateMail container |
| MateMail backend/worker → **engine SMTP submission** | `EMAIL_HOST` | `localhost` — same problem, and separately rejected by the loopback guard |

**Preferred solution for both: a narrow, deterministic host alias.**

Add `extra_hosts` on the MateMail backend, worker and beat services mapping
`mx.matemail.online` to the Docker **bridge gateway address** of the MateMail
network — the address at which the host is reachable from inside those
containers. Then:

- `EMAIL_HOST=mx.matemail.online` — a real public hostname, so TLS validation
  passes against the certificate the engine already needs, and the loopback
  guard is satisfied honestly rather than circumvented. Certificate
  verification stays **on**.
- `MAIL_ENGINE_API_URL=http://mx.matemail.online:8025` — the same name, the
  same resolution, one thing to configure and one thing to get wrong.

Why not simply join the two Docker networks: it would work, and it would also
place the engine's containers and MateMail's containers on a shared L2 segment,
making `matemail_internal`'s isolation and the engine's own network boundaries
a great deal weaker for the sake of avoiding one `extra_hosts` line. Not worth
it, and not done.

**Explicitly unresolved until measured on MateServer in P4B.** The gateway
address is not assumed here. It must be discovered on the real host
(`docker network inspect` of the MateMail app network, plus a reachability test
from inside the running backend container to the engine's ports) and only then
written into configuration. Two things in particular need proving rather than
assuming:

1. That the chosen address is reachable from the MateMail containers' network
   namespace, given the engine binds `127.0.0.1:8025` on the host — a loopback
   bind may not be reachable from a bridge network, in which case the engine's
   HTTP port binds the bridge gateway instead, or host nginx proxies it on an
   internal-only vhost. That choice is made after the test, not before.
2. That `extra_hosts` is honoured by both Python's `smtplib` and `requests` in
   this image — it writes `/etc/hosts`, which the glibc resolver reads, but it
   is worth confirming once rather than inferring.

The engine API is never exposed publicly in any of these options, and
`host.docker.internal` is **not** relied upon — it is not present on Linux
unless explicitly configured.

---

## 9. Mailcow UI and SOGo

The mailcow admin UI is **operator-only**, reachable on loopback behind host
nginx with access control, and never linked from any customer surface. No
customer-facing route resolves to it.

**SOGo cannot simply be removed.** `generate_config.sh` documents `SKIP_SOGO`
as *"experimental, unsupported, not fully implemented"*, and disabling it also
removes DAV and ActiveSync. So SOGo stays running as an internal component of
the engine. It is **not** MateMail webmail, is not branded as such, and is not
linked. P8 remains the phase that builds the real customer webmail (DEC-005r).

This is a documented constraint, not an endorsement: we run it because removing
it is unsupported, and we keep it invisible.

---

## 10. Anti-abuse and relay safety

Design targets for P4B implementation and testing:

| Control | Mechanism |
|---|---|
| No open relay | Submission requires SASL auth on 587/465; port 25 accepts only inbound mail for hosted domains |
| Sender equals authenticated mailbox | Postfix sender restrictions, enforced before `permit_sasl_authenticated` — the P0 audit found this ordering wrong in the documented design and it must be right in the real one |
| Tenant suspension stops submission | `set_domain_active(False)` on suspension; engine refuses auth for the domain's mailboxes |
| Mailbox suspension stops auth | `set_mailbox_active(False)` |
| Domain suspension | Stops submission; inbound handling decided in P5 (reject vs hold is a product choice with deliverability consequences) |
| Quota enforcement | Engine-side per-mailbox quota, set through `ensure_mailbox` |
| Outbound rate policy | Hooks land in P5; the engine's own rate limiter is the enforcement point |
| Rspamd | Retained, default configuration, tuned in P5 |
| **ClamAV** | **Retained.** Not disabled to save memory — see §11 |

---

## 11. Resource plan

Measured read-only on MateServer:

| Resource | Current |
|---|---|
| RAM | 11 GiB total, 3.4 GiB used, **8.3 GiB available** |
| Swap | 4 GiB, 0 B used |
| Disk | 193 GB total, 19 GB used, **175 GB available** |
| CPU | 6 vCPU (AMD EPYC) |
| Docker | 8.2 GB images, 31 containers, 448 MB volumes |

### Estimate

mailcow at `2026-07b` with ClamAV on and Flatcurve FTS at the default 128 MB
heap: roughly **3.5–4.5 GB steady state**, with ClamAV's signature database the
single largest consumer (~1–1.5 GB resident). Peak during signature refresh and
concurrent delivery pushes toward **5–5.5 GB**.

### Verdict

**The existing ~12 GB is sufficient for P4 development and private
validation**, with roughly 3–4 GB headroom after the engine starts. Disk is not
a constraint at 175 GB free.

**The 16 GB upgrade should happen before real customer load**, not before
installation. The margin at 12 GB is adequate for a handful of test domains and
no traffic; it is not adequate for the private beta of 5–10 tenants under
DEC-012, where ClamAV, Rspamd and Dovecot indexing contend with six other
applications. Doing the upgrade first would be defensible; doing it before P4B
production cutover is the latest responsible point.

**ClamAV stays enabled.** It is the antivirus layer for a mail product; turning
it off to reclaim a gigabyte is a security decision disguised as a resource
decision. If measurement later shows genuine pressure, FTS is the component to
reconsider first — it is a search convenience, not a safety control — and that
would be reported before being done.

No swap or tuning change is made in P4A.

---

## 12. Deployment configuration synchronisation

### The weakness P3 exposed

`deploy.yml` updates image tags in `/opt/MateMail/.env` and runs `compose up`.
It never syncs `docker-compose.yml`. P3 found the host running a compose file
from P2.5 that lacked every P3 variable — `DKIM_ENCRYPTION_KEY` among them, so
adding the key to `.env` would have had no effect and DKIM keys would have been
written in plaintext. Images were versioned; runtime configuration was not.

A second trap: this repository is checked out with `core.autocrlf=true`, so the
working copy of `deploy/docker-compose.yml` is CRLF (11857 bytes) while the
committed blob is LF (11604 bytes). Copying the working tree ships a file that
is not the repository version.

### Design

Ship the compose file **as part of the versioned artifact**, from the same
commit as the images, without a server-side checkout:

1. **CI** publishes `deploy/docker-compose.yml` as a release asset keyed by
   commit SHA — or, preferably, bakes it into the backend image at a known path
   (`/opt/deploy/docker-compose.yml`). The image already is the versioned
   artifact; adding the file it should run under keeps one thing to version.
2. **`deploy.yml`** extracts that file from the pulled image
   (`docker create` + `docker cp`, no checkout) and installs it to
   `/opt/MateMail/docker-compose.yml` **after** taking a timestamped backup.
3. **Verification before use:** the workflow compares the installed file's
   SHA-256 against the value recorded for that commit and aborts if they
   differ. Because the file travels inside the image, it is byte-identical to
   the committed blob and the CRLF trap cannot occur — no working tree is
   involved at any point.
4. **A deployment marker** (`/opt/MateMail/.deployed-sha`) records the commit
   that produced the currently installed images *and* compose file, so drift is
   detectable rather than inferred.

This preserves the GHCR artifact model, creates no source checkout on the
server, and makes "images, compose configuration and deployment revision all
correspond to the same commit" a checked property rather than a convention.

Implementation lands in P4B, before the first P4 production deployment.

---

## 13. What P4A did not do

No Mailcow installed, cloned or started. No UFW, iptables, nftables or
DOCKER-USER change. No port opened. No DNS record created or changed. No nginx
change. No certificate issued. `MAIL_ENGINE_ADAPTER` remains `stub` in
production. Nothing deployed. No customer mailbox created. No Git operation.
