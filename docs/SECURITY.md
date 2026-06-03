# SECURITY.md — Security Architecture and Controls

**Product:** MateMail  
**Last updated:** 2026-05-29  
**Classification:** Engineering reference

---

## Principles

1. **No open relay** — SMTP submission requires authentication, always.
2. **Tenant isolation** — A tenant can never read, write, or affect another tenant's data.
3. **Least privilege** — Every API key, service account, and role has the minimum needed scope.
4. **Defense in depth** — Controls at network, application, and data layers.
5. **Fail closed** — Unknown/invalid state defaults to deny, not allow.
6. **No secret logging** — Passwords, tokens, and private keys are never written to logs.

---

## Anti-Relay Controls

### SMTP Submission (Port 587 / 465)

| Rule | Implementation |
|------|---------------|
| Authentication required | Stalwart: no anonymous SMTP submission |
| Only hosted domains allowed as sender | Django pre-send validation + Stalwart FROM domain check |
| Suspended tenant → reject | Django sends disable signal to Stalwart on suspension |
| Disabled mailbox → reject | Stalwart blocks auth for disabled principals |
| Per-mailbox hourly send limit | Stalwart rate limiter + Django enforcement |
| Per-tenant daily send limit | Celery counter in Redis, checked pre-send |

### SMTP Inbound (Port 25)

| Rule | Implementation |
|------|---------------|
| Only accept mail for hosted active domains | Stalwart domain whitelist populated by Django |
| Reject unknown recipient | Stalwart 550 RCPT TO check against known mailboxes |
| Reject unknown domain | Stalwart 550 MAIL FROM domain check |
| SPF check on inbound | Stalwart built-in |
| DKIM verify on inbound | Stalwart built-in |
| DMARC policy on inbound | Stalwart built-in |
| Spam scanning | Stalwart anti-spam engine |
| Rate limit per source IP | Stalwart connection rate limiter |

---

## Authentication Security

### Web Login

| Control | Implementation |
|---------|---------------|
| Password hashing | Django: Argon2 (via `django-argon2`) |
| Password reset tokens | Hashed before storage, single-use, 1-hour TTL |
| JWT tokens | Short-lived access (15 min) + refresh (7 days) |
| Session invalidation | Redis-backed token revocation list |
| 2FA (TOTP) | `django-otp` or `pyotp`, RFC 6238 compliant |
| Backup codes | Hashed before storage, one-time use |
| Login rate limit | 5 attempts per 15 minutes per IP + per account |
| Suspicious login alerts | New IP/country → email alert |
| Session listing | Users can see and revoke active sessions |

### IMAP/SMTP Auth

| Control | Implementation |
|---------|---------------|
| Password hashing | Stalwart: Argon2 or bcrypt |
| Brute-force protection | Stalwart: block IP after N failed attempts |
| TLS required | All auth over TLS (993, 587, 465) — no plaintext auth |
| Disabled mailbox | Stalwart: auth rejected for disabled principals |
| Suspended tenant | Stalwart: auth rejected for all tenant mailboxes |

---

## Tenant Isolation

### Data Layer

Every customer-owned table has a `tenant_id` foreign key. Queryset-level isolation is enforced via:

```python
class TenantScopedManager(models.Manager):
    def get_queryset(self):
        # Automatically filtered by request.tenant in middleware
        ...
```

**No query touches tenant-owned data without an explicit `.filter(tenant=request.tenant)`.** This is enforced in code review and tests.

### API Layer

Every API endpoint that touches tenant data:
1. Checks `request.user.is_authenticated`
2. Resolves `request.tenant` from JWT claim or session
3. Verifies the resource belongs to `request.tenant`
4. Enforces role permissions for the action

### Mail Engine Layer

Domains provisioned in Stalwart are scoped per-domain. Stalwart naturally prevents cross-domain access because each mailbox belongs to exactly one domain, and each domain is owned by exactly one tenant in Django.

There is no API path that allows Tenant A to read or affect Tenant B's mail.

---

## Role-Based Access Control

| Role | Capabilities |
|------|-------------|
| Owner | All capabilities including billing and team management |
| Admin | Domains, mailboxes, aliases, forwarding, DNS, spam, queue, logs, backups, settings |
| Support | View domains, mailboxes, logs, queue; limited actions |
| Read-only | View only; no mutations |
| Platform Admin | Internal staff; cross-tenant management; separate permission check |

Platform admin access is controlled by `User.is_platform_admin` (separate from tenant roles) and enforced by a dedicated `IsPlatformAdmin` DRF permission class on all `/api/platform/` endpoints.

---

## Rate Limiting

| Endpoint / Operation | Limit |
|---------------------|-------|
| POST /api/auth/login/ | 5 / 15 min per IP + 5 / 15 min per account |
| POST /api/auth/signup/ | 3 / hour per IP |
| POST /api/auth/forgot-password/ | 3 / hour per email |
| POST /api/domains/:id/verify/ | 10 / hour per domain |
| POST /api/mailboxes/ | 20 / hour per tenant |
| SMTP AUTH attempts | 5 / min per IP (Stalwart) |
| Outbound SMTP send | 100 / hour per mailbox (configurable) |
| Outbound SMTP send | 1000 / day per tenant (configurable, plan-based) |
| DNS check tasks | 1 / 5 min per domain (Celery) |
| API endpoints (general) | 60 / min per authenticated user |

Implementation: `django-ratelimit` for web endpoints, Stalwart rate limiter for mail protocols.

---

## Secrets Management

| Secret | Storage |
|--------|---------|
| Django SECRET_KEY | Environment variable only, never committed |
| Database password | Environment variable |
| Stalwart API key | Environment variable |
| DKIM private keys | Encrypted at rest in `Domain.dkim_private_key` using Django's `EncryptedField` (django-encrypted-model-fields) |
| JWT signing key | Environment variable |
| Password reset tokens | SHA-256 hashed before DB storage, raw token emailed once |
| 2FA backup codes | SHA-256 hashed before DB storage |
| Stripe API keys (Phase 11) | Environment variable |

**Never committed to git:**
- `.env` files
- Any file matching `*secret*`, `*password*`, `*key*` patterns
- `.gitignore` enforces this

---

## TLS Configuration

| Connection | Minimum TLS | Certificate |
|-----------|-------------|------------|
| HTTPS (443) | TLS 1.2 | Let's Encrypt auto-renew |
| IMAPS (993) | TLS 1.2 | Let's Encrypt auto-renew |
| SMTP TLS (465/587) | TLS 1.2 | Let's Encrypt auto-renew |
| JMAP internal | HTTP (internal Docker network only) | N/A |
| Django ↔ Stalwart API | HTTP (internal Docker network only) | N/A |
| Django ↔ PostgreSQL | TLS or local socket | N/A |
| Django ↔ Redis | Local socket or TLS | N/A |

MTA-STS policy published for each customer domain enforces TLS on inbound mail.

---

## Abuse Prevention

| Threat | Control |
|--------|---------|
| Account takeover | 2FA enforcement, suspicious login alerts, session revocation |
| Open relay | SMTP auth required, FROM domain validation |
| Spam sending | Per-mailbox and per-tenant send limits, reputation monitoring |
| Credential stuffing | Login rate limiting, IP blocking after threshold |
| Tenant enumeration | Workspace slugs not enumerable via public API |
| Mass mailbox creation | Mailbox creation rate limit + plan limits enforced |
| DNS abuse | DNS verification re-checks, domain pause on repeated failures |
| Suspended tenant bypass | Celery task enforces suspension in Stalwart within 60 seconds of status change |

---

## Audit Logging

All significant administrative events are written to `MailLog` with:
- `tenant`
- `event_type`
- `source` (user email or system)
- `ip_address`
- `result`
- `metadata` (JSON, no passwords or secrets)
- `created_at`

Events logged:
- User signup, login, logout, failed login
- Password reset requested / completed
- 2FA enabled / disabled
- Email verification
- Domain added / verified / paused / deleted
- Mailbox created / disabled / deleted / password reset
- Alias created / deleted
- Forwarding rule created / deleted
- DNS verification pass / fail
- Backup completed / failed
- Tenant plan changed
- Tenant suspended / reactivated
- Team member invited / removed
- Role changed
- API key created / revoked
- Platform admin action (separate audit log)

---

## No-Open-Relay Checklist

Before any mail engine phase is considered complete, the following must pass:

- [ ] Unauthenticated SMTP submission rejected with `530 5.7.0`
- [ ] Authenticated submission only accepted for domains owned by the authenticated user's tenant
- [ ] Sending to external recipient without auth rejected
- [ ] Sending from an address not belonging to the authenticated mailbox rejected
- [ ] Suspended tenant: all outbound SMTP rejected
- [ ] Disabled mailbox: SMTP auth rejected
- [ ] Inbound SMTP: unknown domain rejected with `550 5.1.2`
- [ ] Inbound SMTP: unknown recipient rejected with `550 5.1.1`
- [ ] No port 25 unauthenticated forwarding (open relay via inbound → outbound rewrite)
