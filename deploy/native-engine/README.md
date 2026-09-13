# MateMail Native Mail Engine — deployment (NE1)

The Native Engine is a MateMail-owned mail stack built directly on Postfix,
Dovecot and Rspamd. It exists to replace mailcow as the orchestration layer, and
the Private Beta will run on it.

**mailcow is the LIVE production engine and is untouched by everything here.**
It holds the only customer-facing mail path and the platform sender's identity,
and it stays that way until NE8 — which is separately authorised and is not this
phase.

Architecture and the decisions behind it: `docs/NATIVE_MAIL_ENGINE.md` § NE0,
and DEC-019.

---

## What NE1 is

A **foundation**: ten services that start, stay healthy, persist their state and
recover from a restart. Nothing more.

| NE1 delivers | NE1 deliberately does not |
|---|---|
| service topology and configuration | domain / mailbox / alias provisioning (NE2) |
| isolated networks and volumes | DKIM lifecycle (NE2) |
| pinned images | working mail flow (NE3) |
| healthchecks and restart policy | `NativeMailEngineAdapter` (NE5) |
| engine database with versioning | any production mail (NE6+) |

Stub provisioning endpoints were deliberately **not** written. They would make
the stack look finished while doing nothing — a false green this project has
already paid for once.

---

## Isolation — the property that makes this safe

The Native Engine runs beside the live engine, so isolation is not tidiness, it
is the precondition.

```
own network      matemail_native_engine   172.27.0.0/16
own volumes      matemail_native_*        nine, all new
own database     PostgreSQL 16            not mailcow's MySQL
own Redis        not MateMail's, not mailcow's
own signatures   own ClamAV database, not mailcow's
no host ports    none published, at all
no engine link   NE1 reaches neither MateMail nor mailcow
```

Never, at any phase: mount `mailcowdockerized_*`, join `mailcow-network`, or
share the queue, vmail or DKIM volumes. `backend/tests/test_native_engine_deployment.py`
fails the build if any of that appears.

**The subnet was chosen by enumeration, not assumption.** The first candidate,
`172.26.0.0/16`, turned out to belong to `myright_internal` — another NetaMate
application on the same host. Before adding any network, enumerate what exists.
The same rule applies to ports: `ss -tulpn | grep LISTEN` first, always.

---

## Services

| Service | Image source | Egress |
|---|---|---|
| `db` | official `postgres:16-alpine`, digest-pinned | none |
| `redis` | official `redis:7-alpine`, digest-pinned | none |
| `clamav` | official `clamav/clamav`, digest-pinned | **HTTPS** (freshclam) |
| `rspamd` | official `rspamd/rspamd`, digest-pinned | DNS via resolver |
| `dovecot` | **built here** — `images/dovecot/`, derived from the pinned upstream digest | none |
| `api` | `python:3.13-alpine` + mounted source | none |
| `policy` | `python:3.13-alpine` + the P5 bridge | none |
| `postfix` | **built here** — `images/postfix/` | SMTP, DNS |
| `unbound` | **built here** — `images/unbound/` | DNS |
| `olefy` | **built here** — `images/olefy/` | none |

### Images built under repository control

Postfix and Unbound have no official image at all, and neither is a component
to take from an unvetted community source — one carries every outbound message,
the other is the DNSSEC validator that makes DANE meaningful. Olefy is our own
service.

Dovecot is the fourth for a different reason. An official image exists and is
used as the base, but it cannot run the identity model the architecture requires
— see the next section. Ours is a thin **derivative** of that pinned digest: the
Dovecot binaries are byte-for-byte identical, and only `/etc/passwd`,
`/etc/group` and the ownership of four directories differ.

They are built by **GitHub Actions** (`.github/workflows/native-engine-images.yml`,
manual dispatch) and published to GHCR. **Never built on MateServer** — the
production host does not build source.

After CI publishes them, pin the reported digests in the server's `.env`:

```
NATIVE_POSTFIX_IMAGE=ghcr.io/rizwansammo/matemail-native-postfix@sha256:...
NATIVE_DOVECOT_IMAGE=ghcr.io/rizwansammo/matemail-native-dovecot@sha256:...
NATIVE_UNBOUND_IMAGE=ghcr.io/rizwansammo/matemail-native-unbound@sha256:...
NATIVE_OLEFY_IMAGE=ghcr.io/rizwansammo/matemail-native-olefy@sha256:...
```

A tag moves; the digest is what makes the deployment reproducible.

---

## Egress

Docker Compose cannot express a per-service egress policy on its own — a bridge
network gives every container on it the same outbound reach. The table above is
therefore the **intent**, and the limitation is recorded rather than glossed
over.

Only three services have any legitimate reason to reach the Internet: ClamAV for
signatures, Unbound for DNS, and Postfix for SMTP at NE3. Enforcing that
properly needs host-level egress filtering, which touches UFW and is out of
scope here. It belongs with P7's operational work, and the intent is documented
now so it is not discovered later.

---

## Running it

```bash
cd deploy/native-engine
cp .env.example .env          # then fill it in ON THE SERVER, mode 0600
docker compose config --quiet # validate before starting anything
docker compose up -d
docker compose ps             # every service must reach (healthy)
```

Secrets are generated on the server and never committed:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(40))"
```

Production runtime directory is `/opt/MateMailNative/`, following the NetaMate
convention: config under `/opt/<AppName>/`, `.env` at 0600, named volumes, no
source checkout and no builds on the VPS. It must **not** live inside
`/opt/mailcow-dockerized` or `/opt/MateMail`.

---

## Resolver — read this before changing the healthcheck

Postfix runs `smtp_tls_security_level = dane`, and DANE's security comes
entirely from DNSSEC validation. A resolver that answers without validating
looks identical from the outside while the guarantee is gone.

The healthcheck therefore queries **this** resolver explicitly and requires the
`ad` (authenticated data) flag:

```
drill -D @127.0.0.1 cloudflare.com A | grep 'flags:.* ad'
```

The obvious probe, `unbound-host -r`, is a trap: `-r` reads `/etc/resolv.conf`
and asks Docker's resolver instead. It passes with Unbound completely dead —
measured during NE1, not theorised.

Validation confirmed locally on the built image:

```
cloudflare.com      NOERROR + ad flag      (signed, validated)
dnssec-failed.org   SERVFAIL               (bogus, correctly refused)
```

---

## Dovecot — why the engine builds its own image

The architecture fixes the mail store at **vmail 5000:5000** (NE0.4): it is what
mailcow uses, so the NE6 platform-sender maildir migration is a copy rather than
a translation, and it makes storage ownership unambiguous.

The upstream `dovecot/dovecot:2.4.1` image cannot run that. Measured, not
assumed — its `/etc/passwd` carries the distribution accounts plus exactly one
mail account, `vmail` at **1000:1000**, no `dovecot` and no `dovenull`, and it
runs the daemon unprivileged as vmail. Three consequences, all observed:

```
Fatal: service(auth) Group doesn't exist: dovecot      <- then dict, then the next
Fatal: fchown() failed for /run/dovecot/login          <- unprivileged: no separate login user
setgid(5000 from userdb lookup) failed with euid=1000  <- uid 5000 unreachable
```

Collapsing the architecture to uid 1000 would have made all three disappear, and
would have thrown away the reason 5000 was chosen. So the image is fixed to fit
the architecture instead:

| identity | uid | role |
|---|---|---|
| `vmail` | 5000 | owns `/var/vmail` and `/var/vmail_index` — mail access only |
| `dovecot` | system | internal services: auth, dict, stats |
| `dovenull` | system | pre-auth/login — owns no mail, no engine data |

`master` starts as **root**, creates its sockets and runtime directories, then
drops to those three. That is Dovecot's own model. It is **not** a privileged
container: no `privileged: true`, no host networking, no added capabilities —
only the default capability set any root process in a container already has.

Verified on the built image, not inferred from the config:

```
/run/dovecot/login        drwxr-x---  0:998   (dovenull)   <- the fchown that failed upstream
/run/dovecot/auth-userdb  srw-rw-rw-  999:999 (dovecot)
/var/vmail                drwxr-x---  5000:5000
doveadm mailbox create    exit 0, every file created 5000:5000
files owned by uid/gid 1000: 0
dovecot + doveadm binaries: sha256 identical to upstream
```

The upstream image's own `dovecot.conf` appears to validate only because
`!include_try vendor.d/*.conf` names a directory that does not exist. Do not
read that as evidence the defaults are satisfiable there.

---

## NE2 — the provisioning layer

The engine now holds authoritative state for domains, mailboxes, passwords,
quotas, aliases, forwarding and DKIM. **Nothing consumes it yet** — no Postfix
map, no Dovecot lookup, no Rspamd signing path is wired to any of it. That is
NE3.

`matemail-native-api` is the only thing that writes it, and the only place a
DKIM private key exists.

### Endpoints

Authenticated with `NATIVE_API_SECRET` (constant-time compare), reachable only
inside `matemail_native_engine`. RPC-shaped rather than REST because the thing
on the other side is `MailEngineAdapter`, whose operations are verbs with
idempotency guarantees — and "ensure" has no HTTP verb.

```
GET  /health                    unauthenticated, cheap, non-mutating
GET  /ready                     schema version + DKIM storage writability
GET  /status                    operator detail
GET  /v1/domains                POST /v1/domains/{ensure,set-active,delete}
GET  /v1/mailboxes              POST /v1/mailboxes/{ensure,set-active,delete}
GET  /v1/mailboxes/send-as      POST /v1/mailboxes/{set-password,set-quota}
GET  /v1/aliases                POST /v1/aliases/{ensure,delete}
GET  /v1/forwarding             POST /v1/forwarding/ensure
GET  /v1/dkim                   POST /v1/dkim/{rotate,delete}
```

### The API image changed, and the uid matters

NE1 ran this on stock `python:3.13-alpine`. NE2 cannot: Python 3.13 removed the
`crypt` module, so a stock interpreter cannot produce the BLF-CRYPT hash the
pinned Dovecot expects, and it has no RSA generation for DKIM. `images/api/`
carries three exactly-pinned dependencies and nothing else.

It runs as **uid 11333**, which is Rspamd's. That is not cosmetic: a DKIM key
must be mode 0600 *and* readable by Rspamd at NE3, which is only possible if the
writer and the reader are the same uid. An earlier draft ran as `nobody` and
could not even list `/var/lib/rspamd` — mode 0750, no world bits.

The API's access was also **narrowed**. It used to mount all of `native_rspamd`,
giving it write access to Rspamd's bayes database to do a job that touches one
subdirectory. It now mounts a dedicated `native_dkim` volume at the same path
Rspamd interpolates, and Rspamd mounts that volume **read-only**.

### Schema migrations

Applied by the API at startup, under a PostgreSQL advisory lock so two
containers starting together cannot both run migration 002. Each migration and
the row recording it commit together, so a failure leaves neither the change nor
the claim that it happened.

The deployed MateServer database is at version 1 from the NE1 init script. NE2
takes it to version 2. That upgrade path is tested directly — a database
recreated at exactly the version-1 state, migrated forward — as well as from
empty.

### Reading the DKIM guarantees

```
active key         /var/lib/rspamd/dkim/<domain>.<selector>.key   what Rspamd reads
generations        /var/lib/rspamd/dkim/<domain>.<selector>.g<tok>.key
                   immutable, written once, never modified
mode               0600, uid 11333, on the dedicated native_dkim volume that
                   Rspamd mounts READ-ONLY
public material    selector, public_key, dns_record_name, dns_record_value —
                   four fields, enforced by a response guard
private key        never in the database, a response, a log line, or MateMail
```

**The active key file is the single source of truth.** Activation makes the
active path a hard link to one generation — atomic — and
`get_dkim_public_key` DERIVES the public key from whatever is actually there.
The database row is metadata and a cache; when they disagree the file wins and
the row is corrected. So a reader can never be handed a public key that Rspamd
is not signing with.

That is what makes the lifecycle crash-consistent. After process death at any
step the domain has exactly one usable generation and the published key matches
it — or the engine honestly reports no usable key, which reconciliation then
repairs.

```
domain deletion    does NOT delete the key — pinned by the adapter contract,
                   because MateMail's deprovisioning task deletes it explicitly
                   first. Cascading would remove the step that stops the next
                   owner of a domain inheriting the previous owner's key.
```

### The DKIM lifecycle is serialised

Creation, rotation, deletion and reconciliation all run under one PostgreSQL
advisory lock, so two mutations of the same domain can never interleave. Without
it, two concurrent rotations can hand one caller a public key that is not the
signing key — and a later repair cannot take back material that has already been
published.

It is a database lock, not a `threading.Lock`, because the engine is expected to
run more than one API container; it is session-scoped so a killed container
cannot wedge the next rotation; and acquisition has a `lock_timeout` so an
impossible wait fails with a reason instead of hanging.

### Recovering the key store

Reconciliation runs at API startup before the first request is served, and is
available as an authenticated `POST /v1/dkim/reconcile`. It is deliberately
**not** part of `/health` or `/ready`, which must never change state.

It activates a generation a row names but which is not live, corrects a row that
disagrees with the live key, and removes key files no row refers to.

**It never generates a key**, and it refuses to choose between several surviving
generations when the row's pointer is also gone. Which public key was published
is not knowable from inside the engine, and guessing signs mail that fails DKIM —
worse than reporting the domain and letting an operator rotate deliberately.

---

## Status

**NE1 COMPLETE** — deployed and validated on MateServer, 2026-09-13.
**NE2 functionally validated** on MateServer, 2026-09-13 — with a
deployment-integrity fix staged and not yet deployed (see below).

```
runtime                /opt/MateMailNative/   (no git checkout on the VPS;
                       .env is 0600 root:root, secrets generated server-side)
NE1 release            commit a303b7b, CI green, images run 34730345422
NE1 services           10 / 10 healthy, restart-recovered, isolated,
                       no published host ports, UFW and DNS untouched
NE2 local              schema, Native API, crash-consistent DKIM lifecycle,
                       NativeMailEngineAdapter (18 of 26 methods; 8 refuse,
                       naming NE3/NE4) and tests complete. 1231 tests pass.
NE2 runtime            VALIDATED (2026-09-13) — api image published and
                       digest-pinned, engine DB upgraded v1 -> v2, full synthetic
                       lifecycle validated and cleaned, 10/10 healthy
NE2 integrity fix      STAGED, not deployed — the api image now bakes in its own
                       source and the Compose file mounts no host path into it.
                       Republish the api image, pin the new digest, redeploy.
production engine      mailcow, untouched. MAIL_ENGINE_ADAPTER is still "mailcow".
```

### Immutable image digests in production

```
ghcr.io/rizwansammo/matemail-native-api@sha256:198c238c4c3f080cb74f23b0ae65b2b7ce10b38d9fe6fa8d9543cf26588aced6
```

Pinned at NE2B from release `e14e6221`.

**Superseded by the deployment-integrity fix.** That build's image carried only
the dependency set: the application source was bind-mounted from the VPS, so the
digest guaranteed bcrypt, cryptography and psycopg and nothing about the
provisioning logic actually running. The API image now bakes in
`engine/native_api` — source and migrations — and the Compose file mounts no host
path into it. A new digest must be published and pinned before the running API
is covered by its own pin.

`NATIVE_API_IMAGE` is also now **required** rather than defaulted: the old
default named `:ne2`, a tag the workflow never publishes (it publishes the commit
SHA and the moving `ne1`). Failing on the missing variable says what is wrong;
pulling a non-existent tag does not.

### Immutable image digests from NE1

```
ghcr.io/rizwansammo/matemail-native-postfix@sha256:d3aec130fcf42cf8926d278cdbb62944e1ecff8fb8948ae4864745d6e5dde363
ghcr.io/rizwansammo/matemail-native-dovecot@sha256:5ebd68c8712b1baf0c6a527385b4a2cf2c2abfa5f2144d5a4da87e654e975c73
ghcr.io/rizwansammo/matemail-native-unbound@sha256:3149f484719d88aca16d2cab6fa4fff084af47ede134258a1cfb7550d3004d8f
ghcr.io/rizwansammo/matemail-native-olefy@sha256:9a077fe584e821cc8bae7b9607a301a3fbcb8f21260543c8dd589b3b54d69b18
```

`matemail-native-api` joins them at NE2B. All packages are private, and each tag
was checked against its commit-SHA tag so the digest is known to come from that
build rather than trusted.

### What was proven on the deployed NE1 images, not the local ones

```
dovecot   vmail 5000:5000, dovecot 999, dovenull 998 all present;
          /run/dovecot/login is group 998 — the pre-auth identity is NOT vmail;
          socket owners span 0:0, 0:998, 0:999, 999:0, 999:999;
          native_vmail and native_vmail_index volumes are 5000:5000;
          passwd-file holds 0 accounts; every credential denied (exit 77)
postfix   `postfix check` exit 0; inet_protocols = ipv4; 25 and 587 listening
          only INSIDE the container; 0 IPv6 sockets; own queue volume; queue empty
unbound   cloudflare.com NOERROR + ad; dnssec-failed.org SERVFAIL; AAAA NOERROR
clamav    signatures present and current — main.cvd 89 MB, daily.cld 86 MB fetched
          on the day, ClamAV 1.4.6/28115; native signature volume only
olefy     healthy, runs as nobody (65534), 10055 internal, oletools imports
rspamd    resolves redis / clamav / olefy / unbound to 172.27.0.x — all native;
          no mailcow Redis, no mailcow DKIM volume, no DKIM keys present
db/redis  a marker written through schema_version and a synthetic Redis key both
          survived a container restart; probes removed afterwards
```

A note on reading `docker ps`: its Ports column lists a container's **EXPOSE**
declarations, which look alarming for postfix and dovecot. Published mappings are
the ones with an `0.0.0.0:x->` arrow, and there are none — `docker port` returns
empty for all ten, and the host LISTEN count did not change.

NE3 has not started.
