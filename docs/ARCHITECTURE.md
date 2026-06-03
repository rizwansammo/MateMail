# ARCHITECTURE.md — MateMail System Architecture

**Product:** MateMail  
**Version:** 1.0 (Phase 0 draft)  
**Last updated:** 2026-05-29

---

## Overview

MateMail is a multi-tenant SaaS email hosting platform. It is composed of three distinct layers:

1. **SaaS control plane** — Django backend + Next.js frontend
2. **Mail engine** — Stalwart Mail Server (SMTP + IMAP + JMAP)
3. **Supporting infrastructure** — PostgreSQL, Redis, Celery, Nginx

These layers are deliberately separated. Customers interact only with the MateMail UI and API. The mail engine is never exposed directly to customers.

---

## High-Level Architecture Diagram

```
Internet
    │
    ├─── HTTPS (443) ──────────────────────────────────────────────────┐
    │                                                                   │
    │                                              Nginx/Caddy Reverse Proxy
    │                                                   │          │
    │                                            Next.js (3000)   Django API (8000)
    │                                            app.matemail.online
    │                                            webmail.matemail.online
    │
    ├─── SMTP inbound (25) ────── Stalwart Mail Server
    ├─── SMTP submission (587) ── Stalwart Mail Server ──── Authenticated users only
    ├─── SMTPS (465) ─────────── Stalwart Mail Server ──── Authenticated users only
    ├─── IMAP TLS (993) ─────── Stalwart Mail Server ──── Authenticated users only
    └─── IMAP STARTTLS (143) ─── Stalwart Mail Server ──── Authenticated users only

Stalwart Mail Server
    │
    ├─── Provisioning API (REST, internal only) ←── Django control plane
    ├─── PostgreSQL (shared, separate schema) ← virtual domains/users
    ├─── Mail storage (on-disk per mailbox)
    ├─── DKIM signing (built-in, per domain)
    └─── Spam filtering (built-in Sieve + optional Rspamd)

Django Backend
    │
    ├─── PostgreSQL (tenants, users, domains, mailboxes, billing, logs, etc.)
    ├─── Redis (Celery broker + cache)
    ├─── Celery workers (async tasks)
    └─── Stalwart adapter (provisioning layer)

Celery Workers
    ├─── DNS check tasks (periodic + on-demand)
    ├─── DKIM keypair generation
    ├─── Mailbox provisioning sync
    ├─── Mail queue sync
    ├─── Log ingestion from Stalwart
    ├─── Backup jobs
    └─── Billing sync / suspension enforcement
```

---

## SaaS Control Plane

### Django Backend

The Django application is the authoritative source of truth for all tenant, domain, mailbox, billing, and team data.

**Responsibilities:**
- User authentication (JWT/sessions)
- Tenant isolation enforcement
- Domain and mailbox lifecycle management
- DKIM keypair generation and storage
- DNS record generation and verification
- Alias and forwarding rule management
- Billing and plan enforcement
- Team and RBAC management
- Internal platform admin
- Webmail proxy/API (reads from Stalwart JMAP/IMAP)
- Audit logging

**Does NOT:**
- Handle SMTP protocol
- Handle IMAP protocol
- Store mail messages (only metadata)
- Expose mail engine internals to customers

### Next.js Frontend

Three distinct app surfaces served from the same Next.js application:

| Surface | Subdomain | Purpose |
|---------|-----------|---------|
| Marketing site | matemail.online | Landing, pricing, security, docs |
| Admin app | app.matemail.online | Workspace control center |
| Webmail | webmail.matemail.online | End-user email interface |

---

## Mail Engine: Stalwart Mail Server

See [MAIL_ENGINE.md](MAIL_ENGINE.md) for full reasoning.

Stalwart is an all-in-one modern mail server written in Rust. It handles:
- Inbound SMTP (port 25)
- SMTP submission (port 587 STARTTLS, port 465 SMTPS)
- IMAP (port 993 TLS, port 143 STARTTLS)
- JMAP (HTTP/S, internal use by webmail backend)
- DKIM signing
- Basic spam filtering
- Virtual domains and users via configuration or database backend

### Django ↔ Stalwart Integration

The Django `mail_engine` service layer communicates with Stalwart through its **REST management API** (internal network only, never exposed to the internet).

```
Django backend  ──[HTTP POST /api/v1/principal]──►  Stalwart REST API
                                                          │
                                              Stalwart updates its
                                              internal domain/user store
```

All provisioning is synchronous where possible, with a Celery fallback for retries.

---

## Tenant Isolation

Every customer belongs to exactly one **Tenant**. Tenant isolation is enforced at multiple levels:

| Layer | Mechanism |
|-------|-----------|
| Django ORM | Every model has a `tenant` FK; every queryset is filtered by `request.tenant` |
| API permissions | DRF permission classes verify tenant ownership on every endpoint |
| Mail engine | Domains are namespaced by Stalwart domain. Cross-domain delivery is rejected. |
| Mail storage | Stalwart stores mail per-domain per-user in isolated paths |
| PostgreSQL | Row-level tenant scoping (no shared tables without tenant FK) |
| Celery tasks | Task arguments always include tenant_id; workers re-validate scope |

Tenants cannot see, modify, or affect other tenants' data at any layer.

---

## DNS Verification Flow

```
User adds domain in UI
        │
        ▼
Django generates DNS records:
  - MX record (pointing to mx.matemail.online)
  - SPF TXT record
  - DKIM TXT record (public key from generated keypair)
  - DMARC TXT record
  - MTA-STS TXT record
  - TLS-RPT TXT record
        │
        ▼
UI shows copyable DNS record cards
        │
        ▼
User adds records in their DNS provider
        │
        ▼
User clicks "Verify" (or Celery periodic task triggers)
        │
        ▼
Django resolves each DNS record using dnspython
        │
        ▼
Each check stored in DNSRecordCheck model
DNS health score calculated
        │
        ▼
If MX + SPF verified: domain is provisioned as active in Stalwart
If DKIM verified: DKIM signing enabled in Stalwart
Domain status updated (pending/active/warning/failed)
```

---

## Webmail Flow

```
User opens webmail.matemail.online
        │
        ▼
Next.js frontend authenticates user via Django API
        │
        ▼
Django webmail API proxies to Stalwart JMAP endpoint
(JMAP = RFC 8620, modern JSON-based mail access protocol)
        │
        ▼
Message list, folder list, message body returned to frontend
        │
        ▼
User composes message
        │
        ▼
Django webmail /send API receives compose request
        │
        ▼
Django validates: tenant active, mailbox active, send limits OK
        │
        ▼
Django submits message to Stalwart SMTP submission (port 587)
with authenticated mailbox credentials (or via JMAP EmailSubmission)
        │
        ▼
Stalwart sends message, stores copy in Sent folder
        │
        ▼
Sent message visible in webmail via JMAP
```

---

## Admin Dashboard Flow

```
Admin logs into app.matemail.online
        │
        ▼
JWT token issued by Django, scoped to tenant + role
        │
        ▼
Dashboard API: /api/dashboard/overview/
  - Aggregates from Django models (domains, mailboxes, billing)
  - Pulls queue size from Stalwart REST API
  - Pulls recent activity from MailLog model
        │
        ▼
Admin manages domains → Django API → Stalwart provisioning
Admin manages mailboxes → Django API → Stalwart provisioning
Admin views logs → Django MailLog model
Admin views queue → Django proxy to Stalwart queue API
Admin views spam → Django proxy to Stalwart/spam engine
```

---

## Component Inventory

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Frontend | Next.js 14 + TypeScript + Tailwind | UI for all three surfaces |
| Backend | Django 5 + DRF | SaaS control plane API |
| Database | PostgreSQL 16 | Persistent data store |
| Cache/broker | Redis 7 | Celery task queue + API cache |
| Celery workers | Celery 5 | Async/scheduled tasks |
| Mail engine | Stalwart Mail Server | SMTP + IMAP + JMAP |
| Reverse proxy | Nginx / Caddy | HTTPS routing, TLS termination |
| Container runtime | Docker + Docker Compose | Local and production deployment |

---

## Port Map

| Port | Protocol | Service | Access |
|------|----------|---------|--------|
| 80 | HTTP | Nginx redirect | Public |
| 443 | HTTPS | Nginx → Next.js + Django | Public |
| 25 | SMTP | Stalwart inbound | Public (mail servers) |
| 587 | SMTP+STARTTLS | Stalwart submission | Authenticated clients |
| 465 | SMTPS | Stalwart submission | Authenticated clients |
| 993 | IMAPS | Stalwart IMAP | Authenticated clients |
| 143 | IMAP+STARTTLS | Stalwart IMAP | Authenticated clients |
| 3000 | HTTP | Next.js dev | Internal / Docker |
| 8000 | HTTP | Django API | Internal / Docker |
| 5432 | TCP | PostgreSQL | Internal / Docker |
| 6379 | TCP | Redis | Internal / Docker |
| 8080 | HTTP | Stalwart REST API | Internal / Docker only |
