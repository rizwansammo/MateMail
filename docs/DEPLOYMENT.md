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

# Email sending (for Django transactional email)
EMAIL_HOST=localhost
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=noreply@matemail.online
EMAIL_HOST_PASSWORD=<mailbox password>

# JWT
JWT_SECRET_KEY=<strong secret>
JWT_ACCESS_TOKEN_LIFETIME_MINUTES=15
JWT_REFRESH_TOKEN_LIFETIME_DAYS=7
```

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
