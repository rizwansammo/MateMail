# DEPLOYMENT.md — Deployment Guide

**Product:** MateMail  
**Last updated:** 2026-05-29  
**Target:** Linux VPS (Ubuntu 22.04 LTS or Debian 12)

---

## Overview

MateMail is deployed as a Docker Compose stack on a single Linux VPS for MVP. The deployment consists of two compose files that work together:

1. `docker-compose.yml` — MateMail application stack (Django, Next.js, PostgreSQL, Redis, Celery, Nginx)
2. The mailcow stack runs alongside it (mailcow has its own `docker-compose.yml`)

---

## DNS Records Required for the Platform

Before deployment, the following DNS records must be set for `matemail.online`:

| Type | Host | Value | Purpose |
|------|------|-------|---------|
| A | matemail.online | `<VPS_IP>` | Landing page |
| A | portal.matemail.online | `<VPS_IP>` | MateMail Workspace (customers) |
| A | app.matemail.online | `<VPS_IP>` | **Legacy** — 308 redirect to the Workspace only |
| A | platform.matemail.online | `<VPS_IP>` | Platform Console (NetaMate staff) |
| A | postbox.matemail.online | `<VPS_IP>` | PostBox webmail |
| A | autodiscover.matemail.online | `<VPS_IP>` | Outlook mail-client discovery |
| TXT | `_spf` | `v=spf1 ip4:<VPS_IP> -all` | Provider SPF include target (DEC-056) |
| A | docs.matemail.online | `<VPS_IP>` | Documentation |
| A | mx.matemail.online | `<VPS_IP>` | Mail reception hostname |
| A | imap.matemail.online | `<VPS_IP>` | IMAP client config |
| A | smtp.matemail.online | `<VPS_IP>` | SMTP client config |
| MX | matemail.online | 10 mx.matemail.online | Platform's own email |
| PTR | `<VPS_IP>` | mx.matemail.online | Reverse DNS (set at VPS provider) |
| TXT | matemail.online | `v=spf1 ip4:<VPS_IP> ~all` | Platform SPF |
| TXT | mm1._domainkey.matemail.online | DKIM public key | Platform DKIM |
| TXT | _dmarc.matemail.online | `v=DMARC1; p=quarantine; rua=mailto:dmarc@matemail.online` | Platform DMARC |
| TXT | _mta-sts.matemail.online | `v=STSv1; id=<timestamp>` | MTA-STS |
| TXT | _smtp._tls.matemail.online | `v=TLSRPTv1; rua=mailto:tls-reports@matemail.online` | TLS-RPT |

---

## Server Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| CPU | 4 cores | 8 cores |
| RAM | 8 GB | 16 GB |
| Disk | 40 GB SSD | 100 GB SSD |
| Network | 1 Gbps | 1 Gbps |
| IPv4 | Static, 1 address | Static, 1 address |

Note: RAM breakdown:
- mailcow stack: ~3-4 GB
- MateMail app stack: ~2 GB
- OS + headroom: ~2 GB

---

## Required Ports (Firewall Rules)

| Port | Protocol | Purpose | Source |
|------|----------|---------|--------|
| 22 | TCP | SSH | Restricted (your IPs only) |
| 80 | TCP | HTTP (redirect to HTTPS) | 0.0.0.0/0 |
| 443 | TCP | HTTPS | 0.0.0.0/0 |
| 25 | TCP | SMTP inbound | 0.0.0.0/0 |
| 587 | TCP | SMTP submission | 0.0.0.0/0 |
| 465 | TCP | SMTPS | 0.0.0.0/0 |
| 993 | TCP | IMAPS | 0.0.0.0/0 |
| 143 | TCP | IMAP STARTTLS | 0.0.0.0/0 |

Internal ports (NOT exposed publicly):
- 5432 (PostgreSQL)
- 6379 (Redis)
- 8000 (Django)
- 3000 (Next.js dev)
- 8080 (Stalwart/mailcow internal API)

---

## Pre-Deployment Checklist

### VPS Setup
- [ ] Ubuntu 22.04 LTS installed
- [ ] Swap file configured (4 GB recommended)
- [ ] Docker and Docker Compose installed
- [ ] Firewall rules configured (ufw)
- [ ] SSH key-only authentication (password auth disabled)
- [ ] Fail2ban or equivalent installed

### DNS
- [ ] All A records pointing to VPS IP
- [ ] PTR/reverse DNS set at VPS provider
- [ ] MX record for matemail.online set
- [ ] SPF record set for matemail.online

### Secrets
- [ ] All `.env` variables filled in
- [ ] DJANGO_SECRET_KEY generated (50+ random chars)
- [ ] POSTGRES_PASSWORD strong and unique
- [ ] STALWART/MAILCOW API key generated
- [ ] JWT signing key generated
- [ ] DKIM private key will be generated on first run

### TLS Certificates
- [ ] Certificates issued for all subdomains
- [ ] Auto-renewal configured (certbot or Caddy)
- [ ] Certificates covering SMTP and IMAP hostnames

---

## Environment Variables

See `.env.example` for the full list. Key variables:

```bash
# Django
DJANGO_SECRET_KEY=<50+ random chars>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=portal.matemail.online,postbox.matemail.online,platform.matemail.online,app.matemail.online,matemail.online

# Database
POSTGRES_DB=matemail
POSTGRES_USER=matemail
POSTGRES_PASSWORD=<strong password>
DATABASE_URL=postgres://matemail:<password>@postgres:5432/matemail

# Redis
REDIS_URL=redis://redis:6379/0

# Frontend
FRONTEND_URL=https://matemail.online
APP_BASE_URL=https://portal.matemail.online
PLATFORM_BASE_URL=https://platform.matemail.online

# Mail
MAIL_DOMAIN=matemail.online
MAIL_HOSTNAME=mx.matemail.online
IMAP_HOST=imap.matemail.online
SMTP_HOST=smtp.matemail.online
SMTP_SUBMISSION_PORT=587
IMAP_TLS_PORT=993
DKIM_SELECTOR=mm1
MAIL_STORAGE_PATH=/var/mail/vhosts

# Mail engine (mailcow)
MAILCOW_API_URL=http://mailcow-nginx:8080
MAILCOW_API_KEY=<internal secret>

# Transactional application email — MateMail's OWN Mail Engine (DEC-013).
#
# Do NOT set an external SMTP provider here. Leave EMAIL_HOST unset until P4
# builds the engine: an unset or loopback value is detected and logged as an
# error, and the app refuses to send rather than reporting a delivery that did
# not happen. EMAIL_HOST has no default, and it rejects loopback, so the engine
# must be addressed by a named host once it exists.
EMAIL_HOST=            # set in P4 to the Mail Engine host — never localhost
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_TIMEOUT=10
EMAIL_HOST_USER=<provider username / API key id>
EMAIL_HOST_PASSWORD=<provider password / API key>
DEFAULT_FROM_EMAIL=MateMail <noreply@mail.matemail.online>

# DKIM private-key encryption at rest (INTERIM — DEC-007r, removed in P4).
# REQUIRED: `manage.py check --deploy` fails with domains.E001 without it.
# Must differ from DJANGO_SECRET_KEY. Generate on the server:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DKIM_ENCRYPTION_KEY=<fernet key>

# Abuse controls (P3b)
TRUSTED_PROXY_COUNT=1          # host-native nginx is exactly one hop
MAX_WORKSPACES_PER_USER=5

# Refresh-token cookie (P3c)
REFRESH_COOKIE_SECURE=True     # must stay True in production
REFRESH_COOKIE_SAMESITE=Strict

# JWT
JWT_SECRET_KEY=<strong secret>
JWT_ACCESS_TOKEN_LIFETIME_MINUTES=15
JWT_REFRESH_TOKEN_LIFETIME_DAYS=7
```

### Transactional email — delivered by our own Mail Engine (DEC-013)

**Do not configure an external SMTP provider.** MateMail delivers its own
platform mail — account verification, password reset, invitations, security
notices — through the Mail Engine, per DEC-013. No provider account exists, and
no external SMTP credentials are to be created.

Until P4 builds that engine, leave production as follows:

- `EMAIL_HOST` **unset or loopback**, `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD`
  empty. `apps.accounts.mailer` detects this and refuses to send, logging the
  reason, rather than reporting a delivery that did not happen. That is the
  intended state, not an outage to work around.
- `DEFAULT_FROM_EMAIL=MateMail <noreply@mail.matemail.online>` — the sending
  identity is fixed and does not change in P4.
- `MAIL_ENGINE_ADAPTER=stub` until P4 explicitly replaces it.

The application is vendor-neutral `django.core.mail` SMTP, so switching it to
the Mail Engine is a configuration change with no code change. `EMAIL_HOST`
rejects loopback by design, so the engine must be addressed by a named host.

**Outstanding, owned by P4:** the real end-to-end delivery test from
`MateMail <noreply@mail.matemail.online>`, verifying actual inbox delivery plus
SPF, DKIM, DMARC where applicable, PTR/HELO alignment, and no underlying engine
branding leakage. SMTP acceptance alone does not count.

**No MX, SPF, DKIM, DMARC, PTR or HELO record has been created or changed.**

---

## Deployment Steps (Phase 15 — Production)

```bash
# 1. Clone repo
git clone https://github.com/your-org/matemail /opt/matemail
cd /opt/matemail

# 2. Copy and fill in env
cp .env.example .env
nano .env

# 3. Deploy mailcow stack
cd /opt/mailcow-dockerized
./generate_config.sh
docker compose pull
docker compose up -d

# 4. Deploy MateMail app stack
cd /opt/matemail
docker compose pull
docker compose up -d

# 5. Run Django migrations
docker compose exec backend python manage.py migrate

# 6. Create platform superuser
docker compose exec backend python manage.py createsuperuser

# 7. Verify health endpoints
curl https://portal.matemail.online/api/health/
curl https://portal.matemail.online/api/health/db/
curl https://portal.matemail.online/api/health/redis/
curl https://portal.matemail.online/api/health/mail-engine/
```

---

## TLS Certificate Management

### Certificates and the bootstrap order

**Read this before installing any vhost on a host that does not already have
its certificate.** Every MateMail vhost follows the same three-step pattern,
and the reason is here rather than repeated in each runbook.

nginx resolves `ssl_certificate`, `ssl_certificate_key` and `include
/etc/letsencrypt/options-ssl-nginx.conf` when it **parses** its
configuration, not when a request arrives. So enabling a TLS vhost before
certbot has issued the certificate makes `nginx -t` fail:

```
nginx: [emerg] open() "/etc/letsencrypt/options-ssl-nginx.conf" failed
(2: No such file or directory) in /etc/nginx/sites-enabled/matemail-x:NN
```

That failure is **server-wide**, not confined to the new site: `nginx -t`
fails for the whole configuration and a reload attempted anyway refuses to
apply, so every existing MateMail hostname is stuck on its old config. On a
fresh server, nginx will not start at all.

Going the other way round does not work either — HTTP-01 validation needs
something already answering on port 80 for the name. Hence a bootstrap
vhost, one per hostname, sitting beside each real one:

```
deploy/nginx/portal.matemail.online.bootstrap.conf
deploy/nginx/postbox.matemail.online.bootstrap.conf
deploy/nginx/platform.matemail.online.bootstrap.conf
deploy/nginx/autodiscover.matemail.online.bootstrap.conf
```

Each is HTTP-only, serves `/.well-known/acme-challenge/` from
`/var/www/html`, declares no upstream, and 404s everything else. No TLS
directive appears in any of them, so they parse on a server with no
certificates and no running containers.

**The pattern, for any hostname:**

```bash
# 0. DNS first — certbot proves control by being reachable at the name.
dig +short <host>

# 1. Bootstrap vhost, so port 80 answers.
sudo cp deploy/nginx/<host>.bootstrap.conf /etc/nginx/sites-available/<site>
sudo ln -s /etc/nginx/sites-available/<site> /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 2. Certificate, through the webroot the bootstrap vhost serves.
sudo certbot certonly --webroot -w /var/www/html -d <host>

# 3. Overwrite the SAME path with the real vhost, and reload.
sudo cp deploy/nginx/<host>.conf /etc/nginx/sites-available/<site>
sudo nginx -t && sudo systemctl reload nginx
```

Three details that matter:

- **`certonly --webroot`, not `--nginx`.** The nginx plugin rewrites the
  server block it finds. It would edit a file step 3 immediately overwrites,
  leaving the installed config quietly different from the one in Git.
- **Overwrite the same path; do not enable a second file.** Two server
  blocks listening on `:80` for one `server_name` make nginx use whichever
  it parsed first and emit `[warn] conflicting server name … ignored` — a
  confusing way to discover months later that renewals are being served by
  the wrong block.
- **The real vhost keeps its `/.well-known/acme-challenge/` location.**
  certbot renews with the same webroot method that first succeeded, so
  deleting it breaks renewal ninety days later, where it presents as an
  expired certificate rather than as a configuration edit.

**Verify the challenge path before spending an issuance attempt.** Let's
Encrypt rate-limits failures, and this costs nothing:

```bash
sudo mkdir -p /var/www/html/.well-known/acme-challenge
echo bootstrap-ok | sudo tee /var/www/html/.well-known/acme-challenge/matemail-probe >/dev/null
curl -sS http://<host>/.well-known/acme-challenge/matemail-probe   # expect: bootstrap-ok
sudo rm -f /var/www/html/.well-known/acme-challenge/matemail-probe
```

### Which certificate covers what

| Certificate | Names | Served by |
|---|---|---|
| `portal.matemail.online` | portal, apex, www, app (legacy redirect) | Workspace vhost |
| `postbox.matemail.online` | postbox | PostBox vhost |
| `platform.matemail.online` | platform | Platform Console vhost |
| `autodiscover.matemail.online` | autodiscover | Autodiscover vhost |
| `mx.matemail.online` | mx | **Postfix and Dovecot**, not nginx |

The Workspace's four names go in **one** `certonly` call. Issuing them
separately produces four certificates where the vhost expects one, and three
of its server blocks would point at a file whose SANs do not cover them.

`mx.matemail.online` is a separate identity with its own renewal path into
Postfix and Dovecot (see the Native Engine docs). Nothing in this section
touches it.

### Option B — Caddy (Auto-TLS)
Caddy handles certificate issuance and renewal automatically. Add all hostnames to `Caddyfile`.

### Mail TLS (Postfix/Dovecot via mailcow)
mailcow manages its own TLS certificates for Postfix and Dovecot. Ensure Let's Encrypt certs are issued for `imap.matemail.online` and `smtp.matemail.online` and that mailcow is configured to use them.

---

## Monitoring and Maintenance

| Task | Frequency | Tool |
|------|-----------|------|
| Check service health | Continuous | Health endpoint + uptime monitor |
| Rotate logs | Daily | logrotate |
| Backup PostgreSQL | Daily | pg_dump + offsite storage |
| Backup mail storage | Daily | rsync or S3 sync |
| Update Docker images | Monthly | docker compose pull |
| Renew TLS certs | Auto | certbot timer or Caddy |
| Review abuse reports | Weekly | Platform admin portal |
| Check queue backlog | Daily | Admin dashboard |

---

## Production Hardening Checklist

- [ ] `DJANGO_DEBUG=False`
- [ ] `DJANGO_ALLOWED_HOSTS` set correctly
- [ ] Database not exposed to public internet
- [ ] Redis not exposed to public internet
- [ ] Mailcow admin UI not exposed to public internet
- [ ] Django admin accessible only from restricted IPs (or VPN)
- [ ] All secrets in environment variables (not in code)
- [ ] Docker restart policies set (`restart: unless-stopped`)
- [ ] Log rotation configured
- [ ] Fail2ban monitoring SSH, SMTP auth, and IMAP auth
- [ ] PTR record set for outbound IP
- [ ] SPF/DKIM/DMARC configured for matemail.online (platform domain)
- [ ] Open relay test passes
- [ ] TLS test: `testssl.sh` against SMTP and IMAP ports

---

## Client Connection Settings

These are the settings end users enter in their mail clients:

```
Incoming mail (IMAP):
  Server: imap.matemail.online
  Port: 993
  Security: SSL/TLS
  Username: full email address (e.g. ariana@yourdomain.com)
  Password: mailbox password

Outgoing mail (SMTP):
  Server: smtp.matemail.online
  Port: 587
  Security: STARTTLS
  Authentication: Required
  Username: full email address
  Password: mailbox password

Alternative outgoing (SMTPS):
  Server: smtp.matemail.online
  Port: 465
  Security: SSL/TLS
```

---

## Deliverability Checklist

Before sending production email:

- [ ] PTR record matches mail hostname
- [ ] SPF record includes sending IP
- [ ] DKIM signature verifying correctly (use mail-tester.com)
- [ ] DMARC record present and policy set
- [ ] MTA-STS policy published and valid
- [ ] Not listed on major RBLs (check mxtoolbox.com)
- [ ] Test with mail-tester.com (target 9+ / 10)

---

## Mail Engine deployment facts (measured on MateServer, P4B / P4C-A)

Everything in this section was measured on the production host, not planned.
The engine is live and **production is pointed at it** (`MAIL_ENGINE_ADAPTER=mailcow`).

### The private engine link

MateMail reaches the Mail Engine over a **dedicated internal Docker network**
carrying a single TCP passthrough gateway. Nothing is published on a host port,
and no address appears in MateMail's configuration.

```
backend / celery-worker  ──▶  matemail_engine_link  ──▶  matemail-engine-gateway  ──▶  nginx-mailcow:8453
   (and nothing else)         external, internal:true     HAProxy 3.2 LTS, TCP only    postfix-mailcow:587
                              alias: mx.matemail.online
```

| | |
|---|---|
| Link network | `matemail_engine_link` — external, `internal: true`, subnet allocated by Docker |
| Members from MateMail | `backend`, `celery-worker` only |
| Gateway image | `haproxy:3.2.23-alpine`, pinned by digest. HAProxy 3.2 is an LTS branch supported to Q2 2030 |
| Gateway definition | `/opt/mailcow-dockerized/docker-compose.override.yml` (mailcow's supported extension point, in its own `.gitignore`) |
| Gateway config | `/opt/mailcow-dockerized/data/conf/matemail-gateway/haproxy.cfg` |
| Gateway identity on the engine network | `10.244.0.247`, pinned — the engine's API ACL is scoped to it |
| Host-published ports on this path | **none** |

Engine ports remain bound to `127.0.0.1` on the host for operator access over an
SSH tunnel. The application path does not use them.

#### One-time creation — do this before the first deploy

```bash
docker network create --internal matemail_engine_link
```

Created by hand, once, and owned outside both Compose projects so neither
stack's lifecycle can delete a network the other depends on. The deploy workflow
**verifies** it exists and is internal, and refuses to continue otherwise. It
deliberately does **not** create one: a network made with Docker's defaults is a
routable bridge, not an internal one, which silently reopens the exposure the
dedicated link exists to close.

#### Why the engine is not bound to a Docker bridge gateway address

Worth keeping, because the rejected design looks correct.

The engine previously published its ports on MateMail's own app-network gateway,
`172.24.0.1`. They were never public, but they were reachable from **every other
Docker network on this host** — six unrelated application networks opened TCP
connections to them in testing.

**A published bind address selects a destination address; it never restricts the
source.** `docker-proxy` accepts on that socket whichever bridge the traffic
arrives from. Rule counters confirmed the DNAT rule never fired during the test,
so the traffic does not traverse `DOCKER-USER` and a rule there would not have
helped; filtering would have had to happen in `INPUT`, and the socket would
still have been published.

That design also required pinning `matemail_internal` and `matemail_app` to
fixed subnets, because the engine's bind address depended on one of them. **Both
pins have been removed** along with the coupling: Docker allocates, and nothing
outside the compose file depends on which range it picks.

### Outbound TCP/25

Reachable. Connection-and-banner tests from the engine's own network:

| Destination | Result |
|---|---|
| Google (`gmail-smtp-in.l.google.com`, `aspmx.l.google.com`) | reachable |
| Yandex | reachable |
| Apple (`mx01.mail.icloud.com`) | reachable |
| Microsoft (`mx1.hotmail.com`) | **timeout** |

Three independent providers answering establishes that **the hosting provider
imposes no general outbound TCP/25 block** — the question that actually gates
launch.

The Microsoft timeout is consistent with Microsoft-side filtering or reputation
behaviour on a fresh hosting IP, but **the cause was not proven** and should not
be recorded as though it were. It needs investigating before customer mail,
alongside SNDS/JMRP enrollment. No workaround is warranted now.

The IP is not listed on Spamhaus zen, SpamCop or Barracuda.

### PTR — a hard launch blocker, still open

```
169.58.114.252  →  vmi3482362.contaboserver.net     (current)
169.58.114.252  →  mx.matemail.online               (required)
```

Not changed, and deliberately out of scope until the deliverability milestone.
**Do not record this as done.** Forward-confirmed reverse DNS is checked by
every major receiver; a generic hosting PTR that does not match the HELO name is
one of the most reliable ways to land in spam.

### Resources

| | |
|---|---|
| Host RAM | ~11 GiB usable |
| Engine footprint | ~2.3 GB idle (ClamAV ~1.0 GB of it) |
| Available with the engine running | ~6.0 GiB |
| Swap | present, essentially unused |
| Disk | 24 GB of 193 GB |

**Decision:** a 16 GB upgrade is **not** required for engineering and private
validation, and **is** required before enabling real customer mail or a private
beta. The measurement above is of an idle engine — no mailboxes, no queue, no
full-text indexing, no ClamAV scanning under load — while seven other
applications already run on the host.

Do **not** reclaim memory by disabling ClamAV or full-text search. Both are
product requirements, and turning off malware scanning to save a gigabyte on a
mail host is not a trade worth making.

---

## Platform transactional sender

MateMail's own application mail — verification links, password resets,
invitations — is sent through MateMail's own Mail Engine (DEC-013) using a
dedicated service identity.

| | |
|---|---|
| Sender | `MateMail <noreply@mail.matemail.online>` |
| Platform domain | `mail.matemail.online` (5 mailboxes, 1024 MB default and max, 5120 MB total) |
| DKIM selector | `mm1`, 2048-bit, generated and held by the engine |
| Rate limit | **60 messages/hour**, enforced by the engine on the mailbox |
| Submission | `mx.matemail.online:587`, STARTTLS, authenticated |
| Credential | server-only, `/root/.matemail-platform-smtp`, mode 0600 root:root |

**The credential value is never documented, committed, or printed.** It was
generated on the server and exists in exactly two places: that file, and
`/opt/MateMail/.env`.

This identity is not a customer mailbox, not a tenant, and not an admin login.
It is deliberately on a subdomain distinct from every customer domain so the two
reputations, and the two failure modes, stay separate.

### Required DNS for the platform sender

Published on `matemail.online` at Namecheap; the PTR is at Contabo:

| Type | Host | Value |
|---|---|---|
| TXT | `mail` | `v=spf1 ip4:169.58.114.252 -all` |
| TXT | `mm1._domainkey.mail` | the engine's public DKIM key (420 chars) |
| TXT | `_dmarc.mail` | `v=DMARC1; p=none; adkim=s; aspf=s` |
| PTR | `169.58.114.252` | `mx.matemail.online` |

`p=none` is monitoring only and stays that way until reporting and broader
deliverability validation exist. Tightening to quarantine or reject without
somewhere to receive failure reports would break mail with no way to see it.

---

## Mail Engine quota contract — write in MB, read in bytes

A trap worth knowing before writing another probe or touching the adapter: the
engine **writes and reads domain storage under different field names, in
different units.**

| Direction | Field | Unit |
|---|---|---|
| write (`add/domain`, `edit/domain`) | `quota` — domain total | MB |
| write | `maxquota` — per-mailbox ceiling | MB |
| write | `defquota` — new-mailbox default | MB |
| read (`get/domain/<name>`) | `max_quota_for_domain` | **bytes** |
| read | `max_quota_for_mbox` | **bytes** |
| read | `def_quota_for_mbox` | **bytes** |

Reading a domain with the write-side names returns `None` for all three and
looks exactly like the values were never applied. That happened during P4C-B
validation and cost a round of false failures; the values had been correct all
along.

The engine also enforces `defquota <= maxquota <= quota` and refuses the whole
domain otherwise. `quota: 0` does **not** mean unlimited — it is a hard total of
zero, so any positive ceiling above it is a contradiction. See DEC-016.

---

## PostBox deployment (P11)

PostBox adds **no container and no host port**. It is the same frontend image
and the same Django backend, on a third hostname. What it does add is one
credential, one gateway frontend, and a Dovecot image rebuild — and the order
of those matters, because two of them fail loudly if done out of order.

### Order of operations

1. Generate the Dovecot master password and put it in **both** `.env` files.
2. Rebuild and redeploy the Native Engine Dovecot image.
3. Deploy the application images.
4. Add DNS, the nginx vhost and the certificate.

Step 2 before step 1 will not start: the entrypoint refuses to run without
`NATIVE_POSTBOX_MASTER_PASSWORD`, by design (DEC-051) — a Dovecot that started
without the master identity would work for every mail client and fail only for
PostBox, days later, as an unexplained login problem.

### 1 — The Dovecot master credential

One value, written to two places, never to a third:

```bash
# On MateServer, as root. Not echoed to the terminal.
umask 077
PW="$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-40)"

printf 'NATIVE_POSTBOX_MASTER_PASSWORD=%s\n' "$PW" >> /opt/matemail-native-engine/.env
printf 'POSTBOX_MASTER_PASSWORD=%s\n'        "$PW" >> /opt/MateMail/.env
unset PW
```

The two names differ because they are two different systems' views of the same
secret: the engine side is what Dovecot will accept, the application side is
what PostBox will present. They must be equal, and nothing checks that for you
— a mismatch shows up as every PostBox sign-in failing with an authentication
error while IMAP clients work normally.

The character set is restricted deliberately. The value is written into a
Dovecot `passwd-file`, which is colon-delimited, so a `:` in the password would
silently produce a different credential; the entrypoint refuses such a value
rather than accepting it and misbehaving.

This is the one credential on the platform that can open any mailbox. It is not
in Git, not in an image, and not in the application's environment at build
time. Rotating it means changing both files and restarting both sides.

### 2 — Rebuild the Dovecot image

The master `passwd-file` is rendered by the image's entrypoint, so the running
image must be the one that knows how. Build through the
`native-engine-images.yml` workflow with the `dovecot` component, then on the
server:

```bash
cd /opt/matemail-native-engine
docker compose pull dovecot
docker compose up -d dovecot
docker compose logs --tail=40 dovecot     # expect no "refusing" line
```

Verify the master identity actually works before going further — this is the
step that catches a mismatch while it is still cheap:

```bash
docker compose exec dovecot doveadm auth login 'someone@example.com*postbox'
```

### 3 — The IMAPS gateway frontend

`deploy/native-engine/gateway/haproxy.cfg` gains an `imaps` frontend so the
application can reach Dovecot without joining the engine network (DEC-050). It
runs in `mode tcp`, terminates nothing and holds no certificate — Dovecot still
sees the TLS session.

```bash
docker compose up -d gateway
```

Check it from the consumer rather than from the gateway — what matters is that
the *backend* can complete an IMAP greeting, which is the whole path:

```bash
docker compose -f /opt/MateMail/docker-compose.yml exec backend python - <<'PY'
import imaplib, os
host = os.environ["POSTBOX_IMAP_HOST"]
with imaplib.IMAP4_SSL(host, 993, timeout=10) as c:
    print(host, "->", c.welcome.decode())
PY
```

Dovecot must **not** appear on `matemail_engine_link`. If it does, the
application can also reach LMTP, which has no authentication:

```bash
docker network inspect matemail_engine_link \
  --format '{{range .Containers}}{{println .Name}}{{end}}'
# expect the Native API and the MateMail backend only
```

### 4 — DNS, nginx and TLS

The three-step bootstrap order and the reasoning behind it are in
§ *Certificates and the bootstrap order*. Applied here:

```bash
# 0. DNS: A  postbox.matemail.online → <VPS_IP>
dig +short postbox.matemail.online

# 1. HTTP-only bootstrap vhost — no TLS directives, so it parses with no
#    certificate on disk.
sudo cp deploy/nginx/postbox.matemail.online.bootstrap.conf \
        /etc/nginx/sites-available/matemail-postbox
sudo ln -s /etc/nginx/sites-available/matemail-postbox /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 2. Certificate.
sudo certbot certonly --webroot -w /var/www/html -d postbox.matemail.online

# 3. Overwrite the same path with the real vhost. The symlink already
#    exists and is not recreated.
sudo cp deploy/nginx/postbox.matemail.online.conf \
        /etc/nginx/sites-available/matemail-postbox
sudo nginx -t && sudo systemctl reload nginx
```

Neither file redeclares the `matemail_backend` / `matemail_frontend`
upstreams — those are declared once in the Workspace vhost, and a second
declaration stops nginx starting. The bootstrap vhost declares no upstream at
all, which is why it parses before the MateMail containers are running.

`postbox.matemail.online` must also be in `DJANGO_ALLOWED_HOSTS`, or every
request returns 400 with `DisallowedHost` before it reaches a view.

### 5 — Verify, and mean it

```bash
curl -sI https://postbox.matemail.online/            # 200, X-Frame-Options: DENY
curl -sI https://postbox.matemail.online/api/internal/health/   # 403 from nginx
curl -s  https://portal.matemail.online/postbox -o /dev/null -w '%{http_code}\n' # 308
curl -s  https://app.matemail.online/         -o /dev/null -w '%{http_code}\n' # 308 legacy
```

Then sign in as a real mailbox and check four things that only a real session
proves: the Inbox lists messages, opening one renders sanitised HTML with
remote images blocked, sending arrives at an external address, and the sent
copy appears in Sent. An endpoint returning 200 is not evidence for any of
them.

### What PostBox does not need

No new firewall rule, no new published port, no separate container, no
third-party webmail, and no mailbox password stored anywhere. If a deployment
step seems to ask for one of those, it is the wrong step.

---

## PostBox remote push (optional, not yet deployed)

Remote new-mail push for the native PostBox apps. Design and client contract:
`docs/POSTBOX_REMOTE_PUSH.md` (DEC-058). Every setting is optional, and mail
delivery depends on none of them. Leave them empty and nothing is sent.

What it adds:

- **Two tables.** Migration `postbox 0005`, additive only, applied like any
  other migration.
- **Two endpoints.** `/api/postbox/devices/` is part of the PostBox API and
  authenticated by the PostBox session. `/api/internal/postbox/push-events/`
  is internal, and host nginx already denies it at the edge.
- **Two beat tasks,** plus one new dependency, `google-auth`.
- **Nothing on the network side.** No new port, container, network or nginx
  change.

Order, when it is switched on:

1. **MateMail.** Deploy the release that contains it: the backend image
   carries the code and `google-auth`. In `/opt/MateMail/.env`, generate
   `POSTBOX_PUSH_INGEST_SECRET`
   (`python -c "import secrets; print(secrets.token_urlsafe(40))"`). It must
   not be `INTERNAL_API_SECRET`. Recreate `backend`, `celery-worker` and
   `celery-beat`.
2. **Native Engine.** In `/opt/MateMailNative/.env`:
   - set `NATIVE_POSTBOX_PUSH_URL=http://backend:8000/api/internal/postbox/push-events/`;
   - set `NATIVE_POSTBOX_PUSH_SECRET` to the same value as the ingest secret;
   - generate a **separate** `NATIVE_DOVECOT_PUSH_SECRET`.

   Then deploy the new API and Dovecot images with `./deploy.sh` (see
   `deploy/native-engine/README.md`).
3. **Providers**, when their accounts exist:
   - **FCM:** mount the service-account JSON read-only into `backend` and
     `celery-worker` (the commented example in `deploy/docker-compose.yml`),
     then set `POSTBOX_FCM_ENABLED=True`, `POSTBOX_FCM_PROJECT_ID` and
     `POSTBOX_FCM_CREDENTIALS_FILE`.
   - **WNS:** set `POSTBOX_WNS_ENABLED=True`, `POSTBOX_WNS_TENANT_ID`,
     `POSTBOX_WNS_CLIENT_ID` and `POSTBOX_WNS_CLIENT_SECRET`.

A step done out of order costs a warning line and no push, never mail. Check
it with the commands in the engine README. On the MateMail side, the log line
`PostBox push: event <id> dispatched to <n> device(s)` shows events arriving.

---

## Workspace hostname migration — app → portal (DEC-055)

The customer console moved from `app.matemail.online` to
`portal.matemail.online`. The old name keeps working as a 308 redirect and
serves nothing (DEC-055).

Order matters: the certificate must cover the new name before nginx is asked
to serve it, and the new name must resolve before certbot can prove it.

```bash
# 1. DNS, first. A  portal.matemail.online → <VPS_IP>
#    Leave the app.matemail.online record in place — the redirect needs it.
dig +short portal.matemail.online

# 2. One certificate covering both names, plus the apex.
#    --webroot, not --nginx: the nginx plugin would rewrite the currently
#    installed vhost, which step 3 is about to replace. On this server the
#    existing app.matemail.online vhost already answers on port 80, so the
#    challenge is reachable without a bootstrap vhost; on a rebuilt host,
#    use portal.matemail.online.bootstrap.conf first (see § Certificates and
#    the bootstrap order).
sudo certbot certonly --webroot -w /var/www/html \
  -d portal.matemail.online -d matemail.online -d www.matemail.online \
  -d app.matemail.online

# 3. Replace the vhost. The file was renamed, so remove the old symlink —
#    leaving both enabled is a duplicate-upstream error and nginx will not start.
sudo rm -f /etc/nginx/sites-enabled/matemail
sudo cp deploy/nginx/portal.matemail.online.conf \
        /etc/nginx/sites-available/matemail
sudo ln -s /etc/nginx/sites-available/matemail /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 4. Application config. Both names stay in ALLOWED_HOSTS: the redirect is
#    served by nginx, but anything that reaches Django on the old name during
#    the cutover must not 400.
#    In /opt/MateMail/.env:
#      DJANGO_ALLOWED_HOSTS=portal.matemail.online,postbox.matemail.online,\
#                           platform.matemail.online,app.matemail.online,matemail.online
#      FRONTEND_URL=https://portal.matemail.online
#      APP_BASE_URL=https://portal.matemail.online
#      CORS_ALLOWED_ORIGINS=https://portal.matemail.online,...
docker compose -f /opt/MateMail/docker-compose.yml up -d backend celery-worker celery-beat
```

### The frontend image must be rebuilt

`NEXT_PUBLIC_*` values are inlined at build time, so changing them in `.env`
does nothing to the browser bundle. A frontend still built with the old
hostname will keep treating `app.matemail.online` as the Workspace and will not
redirect it. Rebuild through CI and redeploy:

```bash
docker compose -f /opt/MateMail/docker-compose.yml pull frontend
docker compose -f /opt/MateMail/docker-compose.yml up -d frontend
```

### Verify

```bash
curl -sI https://portal.matemail.online/login | head -1     # 200
curl -sI https://app.matemail.online/login    | head -1     # 308
curl -sI https://app.matemail.online/login | grep -i location
# expect: location: https://portal.matemail.online/login   (path preserved)
```

Path preservation is the thing to actually check. A redirect that drops the
path sends everyone to a login page instead of where their bookmark pointed,
and it looks like it is working.

### Retiring the old name

Not on a date. The access log is the only evidence of who still uses it:

```bash
sudo awk '$0 ~ /app\.matemail\.online/ {n++} END {print n+0}' \
  /var/log/nginx/access.log
```

When that is durably zero: delete the legacy server block, drop the name from
the port-80 block and from the certificate, and drop
`NEXT_PUBLIC_LEGACY_WORKSPACE_HOSTS` from the frontend build.

---

## Autodiscover deployment (Mail Client Discovery phase)

Adds no container and no host port. One hostname, one certificate, one nginx
vhost, and two settings already defaulted in the compose file.

**Nothing here has been done.** `autodiscover.matemail.online` has no DNS
record, no certificate and no installed vhost.

### Order, and why it is this order

1. **DNS A record** — `autodiscover.matemail.online` → `169.58.114.252`.
   First, because certbot proves control over the name by being reachable at
   it.
2. **Deploy the application** — the endpoint must answer before a certificate
   for it is worth having, and `autodiscover.matemail.online` must be in
   `DJANGO_ALLOWED_HOSTS` or every request is a 400 before reaching a view.
3. **HTTP-only bootstrap vhost**, so port 80 answers for the name.
4. **Issue the certificate** with `certonly --webroot`.
5. **Replace the bootstrap vhost with the real one** and reload.
6. **Only then** tell customers to publish the SRV record.

Steps 3–5 are three steps rather than one because of a genuine
chicken-and-egg: nginx resolves `ssl_certificate` when it PARSES the
configuration, not when a request arrives. Installing the final vhost before
the certificate exists makes `nginx -t` fail —

```
nginx: [emerg] cannot load certificate
".../autodiscover.matemail.online/fullchain.pem": BIO_new_file() failed
(SSL: error:80000002:system library::No such file or directory)
```

— and that failure is **server-wide**, not confined to the new site. Every
MateMail hostname stops reloading with it. Going the other way round does not
work either: HTTP-01 validation needs something already answering on port 80
for that name, which is exactly what the bootstrap vhost provides.

Step 4 last is the one that matters. An SRV record pointing at a host that does
not answer is worse than no record: Outlook follows it, fails, and stops
looking — where with no record it would have fallen through to manual setup
that works.

```bash
# ── 1 — DNS, and confirm it resolves before going further ────────────────
dig +short autodiscover.matemail.online       # expect 169.58.114.252

# ── 2 — application ──────────────────────────────────────────────────────
#   In /opt/MateMail/.env, add autodiscover.matemail.online to
#   DJANGO_ALLOWED_HOSTS. SPF_INCLUDE_DOMAIN and AUTODISCOVER_HOST have
#   correct defaults in docker-compose.yml and need no entry unless you are
#   overriding them.
docker compose -f /opt/MateMail/docker-compose.yml up -d backend

# ── 3 — HTTP-only bootstrap vhost ────────────────────────────────────────
#   No TLS directives at all, so this parses with no certificate on disk.
sudo cp deploy/nginx/autodiscover.matemail.online.bootstrap.conf \
        /etc/nginx/sites-available/matemail-autodiscover
sudo ln -s /etc/nginx/sites-available/matemail-autodiscover \
           /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

#   Prove the ACME path is reachable BEFORE spending a rate-limited
#   Let's Encrypt issuance attempt on finding out that it is not.
sudo mkdir -p /var/www/html/.well-known/acme-challenge
echo bootstrap-ok | sudo tee /var/www/html/.well-known/acme-challenge/matemail-probe >/dev/null
curl -sS http://autodiscover.matemail.online/.well-known/acme-challenge/matemail-probe
#   expect: bootstrap-ok
sudo rm -f /var/www/html/.well-known/acme-challenge/matemail-probe

# ── 4 — certificate, via the existing webroot ────────────────────────────
#   --webroot, not --nginx: the nginx plugin rewrites the server block it
#   finds, which would edit a file step 5 immediately overwrites and leave
#   the installed config differing from the one in Git.
sudo certbot certonly --webroot -w /var/www/html \
     -d autodiscover.matemail.online
sudo ls -l /etc/letsencrypt/live/autodiscover.matemail.online/fullchain.pem

# ── 5 — the real vhost, over the same path ───────────────────────────────
#   The symlink already points here, so it is not recreated.
sudo cp deploy/nginx/autodiscover.matemail.online.conf \
        /etc/nginx/sites-available/matemail-autodiscover
sudo nginx -t && sudo systemctl reload nginx
```

Do not leave the bootstrap file enabled alongside the real one. Two server
blocks listening on `:80` for the same `server_name` make nginx use whichever
it parsed first and emit a `conflicting server name` warning — a confusing
way to find out months later that renewals are being served by the wrong
block. Copying over the same path, as above, avoids this by construction.

The real vhost keeps an `/.well-known/acme-challenge/` location on port 80,
and it must: certbot renews through the same webroot method that first
succeeded. Removing that location breaks renewal ninety days later, where it
presents as an expired certificate rather than as a configuration edit.

Neither vhost redeclares `matemail_backend` — that upstream is declared once,
in `portal.matemail.online.conf`, and a second declaration stops nginx
starting.

### Verify

```bash
cat > /tmp/ad.xml <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<Autodiscover xmlns="http://schemas.microsoft.com/exchange/autodiscover/outlook/requestschema/2006">
  <Request>
    <EMailAddress>someone@a-hosted-domain.example</EMailAddress>
    <AcceptableResponseSchema>http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a</AcceptableResponseSchema>
  </Request>
</Autodiscover>
XML

# Settings for a hosted domain.
curl -sS -X POST https://autodiscover.matemail.online/autodiscover/autodiscover.xml \
     -H 'Content-Type: text/xml' --data @/tmp/ad.xml
# expect: <Type>IMAP</Type> … <Port>993</Port> … <Type>SMTP</Type> … <Port>587</Port>
#         and <Encryption>TLS</Encryption> on the SMTP block, NOT <SSL>on</SSL>

# The host serves nothing else. Each of these must be 404.
for p in / /login /app /postbox /platform /django-admin/ /api/health/ /api/internal/health/; do
  printf '%-26s %s\n' "$p" \
    "$(curl -s -o /dev/null -w '%{http_code}' https://autodiscover.matemail.online$p)"
done
```

That second loop is the one worth running twice. This vhost is an allow-list
with a default of 404, and the whole design rests on nothing else being
reachable on a hostname nobody is watching.

### Telling a customer to publish the SRV record

Only after the checks above pass. In the Workspace, the domain page shows it
under **Mail Client Discovery**, separate from MX/SPF/DKIM/DMARC and excluded
from the health score.

```
_autodiscover._tcp.<customer-domain>.   SRV   0 0 443 autodiscover.matemail.online.
```

A customer is never asked for `autodiscover.<their-domain>` as a CNAME: our
certificate would not be valid for their name, so that would need a certificate
per customer domain (DEC-057).

### Rollback

Three independent steps, in decreasing order of urgency:

1. Tell affected customers to delete the SRV record, or delete it for any
   domain where MateMail manages DNS. This is what stops clients being sent to
   a broken endpoint.
2. `sudo rm /etc/nginx/sites-enabled/matemail-autodiscover && sudo nginx -t &&
   sudo systemctl reload nginx` — the hostname stops answering. Removing the
   symlink is enough; leave the certificate in place, so re-enabling later
   does not need the bootstrap step again.
3. Redeploy the previous backend image if the endpoint itself is the problem.

No mail flow depends on any of this. Removing all three leaves IMAP, SMTP,
delivery, SPF, DKIM and DMARC exactly as they were; clients configured by hand
are unaffected, and clients configured through Autodiscover keep the settings
they already have.

The SPF change is separate and does **not** roll back with the above: a
customer who has published `include:_spf.matemail.online` should leave it.
Reverting `SPF_INCLUDE_DOMAIN` to `matemail.online` would mark every updated
customer's SPF as failed.
