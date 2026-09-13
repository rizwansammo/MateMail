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

## Status

```
NE1 configuration      complete, in this directory
image builds           all four built locally and validated:
                         postfix  `postfix check` clean, pgsql map driver
                                  present, daemon starts via its entrypoint and
                                  listens on 25/587 INSIDE the container with no
                                  host port and no IPv6 socket
                         dovecot  vmail 5000:5000, dovenull and dovecot present,
                                  master drops privileges, mail worker reads AND
                                  writes a 5000:5000 maildir, all credentials
                                  denied, upstream binaries byte-identical
                         unbound  DNSSEC validation proven (see above)
                         olefy    starts as nobody, 10055 listening, healthcheck
                                  passes, oletools 0.60.2 actually loaded
local validation       db / redis / api / policy started, healthy, state
                       survived a restart. Dovecot validates against its own
                       image, starts, passes its healthcheck, survives a restart
                       and DENIES every credential — proven by mutation, not by
                       reading the file.
MateServer runtime     BLOCKED — the four repository-controlled images are not
                       yet published to GHCR. Dispatch the Native Engine images
                       workflow, pin the digests, then deploy. A local build is
                       not a published image.
resources              safe: 5.5 GiB available, 4 GiB swap unused, 169 GB free.
                       ClamAV is the heavy item (~1 GB) and is memory-bounded.
```
