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
| A | app.matemail.online | `<VPS_IP>` | Admin dashboard |
| A | webmail.matemail.online | `<VPS_IP>` | Webmail |
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
DJANGO_ALLOWED_HOSTS=app.matemail.online,webmail.matemail.online,matemail.online

# Database
POSTGRES_DB=matemail
POSTGRES_USER=matemail
POSTGRES_PASSWORD=<strong password>
DATABASE_URL=postgres://matemail:<password>@postgres:5432/matemail

# Redis
REDIS_URL=redis://redis:6379/0

# Frontend
FRONTEND_URL=https://matemail.online
APP_BASE_URL=https://app.matemail.online
WEBMAIL_BASE_URL=https://webmail.matemail.online

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
curl https://app.matemail.online/api/health/
curl https://app.matemail.online/api/health/db/
curl https://app.matemail.online/api/health/redis/
curl https://app.matemail.online/api/health/mail-engine/
```

---

## TLS Certificate Management

### Option A — Certbot (Nginx)
```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d matemail.online -d app.matemail.online \
  -d webmail.matemail.online -d docs.matemail.online \
  -d imap.matemail.online -d smtp.matemail.online \
  -d mx.matemail.online
```

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
The engine is installed and validated; MateMail is **not yet pointed at it**.

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
