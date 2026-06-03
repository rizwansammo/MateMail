---
name: project-matemail
description: MateMail — SaaS email hosting platform; phases 1-15 complete; Phase 16 (testing) is next; Django backend + Next.js frontend; mailcow mail engine; multi-tenant JWT + API key architecture
metadata:
  type: project
---

MateMail is a full production-grade SaaS email hosting platform. Built from scratch at C:\Users\Rizwan\Desktop\MateMail. Owner: joe@netswitch.net.

**Why:** Commercial product under domain matemail.online targeting business teams and agencies.

**Current phase:** Phase 16 — Testing and quality (next up)

---

## Stack

- Frontend: Next.js 16.2.6, TypeScript, Tailwind v4, App Router — `frontend/`
- Backend: Django 5, DRF, split settings (base/dev/prod) — `backend/`
- DB: PostgreSQL 16 (MateMail data), MariaDB inside mailcow (mail delivery data)
- Async: Celery 5 + Redis 7
- Mail engine: Mailcow (Postfix + Dovecot + Rspamd via mailcow REST API)
- Proxy: Nginx (HTTP dev, TLS in Phase 15)
- Containers: Docker Compose (`docker-compose.yml` MateMail stack, `docker-compose.mailengine.yml` mail engine)
- VPS: Contabo, IP `176.57.188.13`, AS51167, France — all 60+ blacklists clean, AbuseIPDB 0%

---

## Key architecture rules (NEVER violate)

- Never expose mailcow/Postfix/Dovecot/Rspamd names in customer UI — always say "MateMail"
- Never create an open relay — SMTP submission must require authentication
- Always tenant-scope every DB query via `TenantScopedManager.for_tenant(tenant)`
- `MailEngineAdapter` abstract interface is the ONLY code that touches the mail engine API
- Inbound SMTP only for verified active domains; outbound only for authenticated active mailboxes
- Mailbox passwords: sync provisioning in same call stack, never stored, never queued via Celery
- `INTERNAL_API_SECRET` guards all `/api/internal/` endpoints; nginx blocks public access
- Do not expose engine names in any customer-facing error messages — `_sanitize()` in mailcow_adapter strips them

---

## Phases completed

| Phase | Title | Key deliverables |
|-------|-------|-----------------|
| 0 | Repo audit + plan | Full 17-phase roadmap |
| 1 | Foundation | Docker, ENV, health endpoints, MailEngineAdapter interface |
| 2 | Core models | 16 Django apps, all models + migrations, TenantScopedManager, TenantMiddleware, RBAC, 15/15 isolation tests |
| 3 | Auth | JWT (tenant_id claim), signup/login/logout/2FA/reset, email verification, frontend auth pages |
| 4 | Workspace onboarding | Workspace APIs, domain/mailbox basic CRUD, 6-step onboarding wizard, protected app layout |
| 5 | DNS + DKIM | DKIM 2048-bit RSA gen, real DNS checks (dnspython), DNS health score, Celery beat 15-min check |
| 6 | Mail engine integration | MailcowAdapter (full REST), StubAdapter, Celery tasks for domain provision/deprovision |
| 7 | SMTP/IMAP/Webmail data flow | Inbound/outbound policy endpoints, Redis rate limiter, Webmail SSO bridge, mailbox enable/disable |
| 8 | Admin dashboard + control center | Live stats dashboard, settings page, team management (add/role/remove), platform admin API |
| 9 | Aliases + forwarding | Full alias CRUD + enable/disable, forwarding rules with multi-dest sync, mailcow adapter implementation |
| 10 | Spam, quarantine, queue, logs | MailLog audit trail + log_event() utility, queue API + cancel, quarantine API + release/delete, 3 frontend pages |
| 11 | Billing, plans, limits, trial | Trial plan (30 days, no Stripe), 4 plan tiers, plan limit enforcement, expire_trials Celery task, admin assign-plan API, billing frontend page |
| 12 | Team invites, API keys | TeamInvite model + email invite flow, APIKey model + CRUD, APIKeyAuthentication DRF class (Bearer mm_…), TenantMiddleware API key support, accept-invite frontend page |
| 13 | Internal platform admin portal | GET /api/platform/stats/, GET /api/platform/tenants/{id}/, is_platform_admin in UserProfileSerializer, dark /admin layout with guard, dashboard + tenant list + tenant detail with suspend/activate/plan/trial management |
| 14 | Backups and restore visibility | BackupJob serializers + views, GET/POST /api/backups/, GET /api/backups/{id}/, run_backup_task Celery task (pending→running→completed/failed), /app/backups frontend with stats, filters, job table, auto-poll, expand detail |
| 15 | Deployment, TLS, DNS, production hardening | nginx TLS config (matemail-tls.conf), docker-compose.prod.yml, postfix_policy_bridge.py asyncio daemon, init-letsencrypt.sh, apply-mailcow-config.sh, deploy.sh, Django admin renamed /django-admin/, prod.py hardening + logging |

---

## Repository structure (key files)

### Backend apps
```
backend/apps/
  accounts/       — User model, JWT tokens, 2FA, email/password reset
  tenants/        — Tenant, TenantMembership, TenantScopedManager, TenantMiddleware, RBAC
  domains/        — Domain model, DNS health, DKIM
  mailboxes/      — Mailbox model, enable/disable, re-provision
  aliases/        — Alias CRUD, enable/disable (Phase 9)
  forwarding/     — ForwardingRule CRUD, pause/disable (Phase 9)
  mail_engine/    — MailEngineAdapter interface, MailcowAdapter, StubAdapter, Celery tasks
  smtp_policy/    — Inbound/outbound policy views, Redis rate limiter
  webmail/        — SSO token generation + validation
  platform_admin/ — IsPlatformAdmin: tenant list, suspend, activate
  dnshealth/      — DNSRecordCheck model, Celery beat task
  health/         — /api/health/* endpoints
  billing/        — Plan, Subscription, Invoice (Stripe-ready, Phase 11)
  teams/          — (Phase 12)
  logs/           — MailLog (Phase 10)
  mailqueue/      — QueueMessage (Phase 10)
  quarantine/     — QuarantineMessage (Phase 10)
  backups/        — BackupJob (Phase 14)
```

### Key backend files
| File | Purpose |
|------|---------|
| `apps/mail_engine/adapter.py` | Abstract MailEngineAdapter interface |
| `apps/mail_engine/mailcow_adapter.py` | Full mailcow REST API implementation |
| `apps/mail_engine/stub_adapter.py` | No-op adapter for local dev (MAIL_ENGINE_ADAPTER=stub) |
| `apps/mail_engine/factory.py` | `get_adapter()` singleton |
| `apps/mail_engine/tasks.py` | Celery tasks: provision_domain_task, deprovision_* |
| `apps/tenants/managers.py` | TenantScopedManager — `.for_tenant(tenant)` |
| `apps/tenants/middleware.py` | TenantMiddleware — injects request.tenant from JWT |
| `apps/tenants/permissions.py` | IsPlatformAdmin, IsTenantOwner, IsTenantAdmin, IsTenantSupport, HasTenantAccess |
| `apps/smtp_policy/rate_limits.py` | Redis rate limiter: 100/h mailbox, 500/h domain, 2000/day tenant |
| `config/urls.py` | All URL routes |
| `config/settings/base.py` | All settings including INTERNAL_API_SECRET, MAIL_ENGINE_ADAPTER |

### API endpoints (all implemented)
| Method | Endpoint | Notes |
|--------|----------|-------|
| POST | `/api/auth/signup/` | Creates user + tenant + JWT |
| POST | `/api/auth/login/` | Returns JWT or 2FA partial token |
| POST | `/api/auth/2fa/verify/` | Completes 2FA login |
| GET | `/api/workspaces/{id}/stats/` | Live domain/mailbox/storage/member counts |
| GET/POST | `/api/workspaces/{id}/members/` | List + add members |
| PATCH/DELETE | `/api/workspaces/{id}/members/{mid}/` | Change role / remove |
| GET/POST | `/api/domains/` | Domain list + create |
| POST | `/api/domains/{id}/provision/` | Re-queue mail engine provisioning |
| GET/POST | `/api/mailboxes/` | Mailbox list + create (sync provisioning, password never stored) |
| PATCH | `/api/mailboxes/{id}/status/` | Enable/disable + mail engine sync |
| POST | `/api/mailboxes/{id}/reprovision/` | New password, mail engine sync |
| GET | `/api/billing/` | Subscription + plan details + usage + trial_days_left |
| POST | `/api/platform/tenants/{id}/plan/` | Assign plan (`plan_tier`) or extend trial (`extend_trial_days`) |
| GET | `/api/logs/` | Audit log list (paginated, filters: event_type, result, search, mailbox_id, domain_id) |
| GET | `/api/queue/` | Mail queue list (filter by status; default: active only) |
| POST | `/api/queue/{id}/cancel/` | Cancel a queued/deferred message |
| GET | `/api/quarantine/` | Quarantine list (filter by status/sender; default: held) |
| POST | `/api/quarantine/{id}/release/` | Release held message to inbox |
| DELETE | `/api/quarantine/{id}/` | Mark quarantine message as deleted |
| GET | `/api/teams/invites/` | List pending invites for tenant (IsTenantAdmin) |
| POST | `/api/teams/invites/` | Send email invite (IsTenantAdmin) |
| GET | `/api/teams/invites/preview/?token=` | Public — invite metadata (for accept page) |
| POST | `/api/teams/invites/accept/` | Accept invite with token (IsAuthenticated, email must match) |
| DELETE | `/api/teams/invites/{id}/` | Revoke pending invite (IsTenantAdmin) |
| GET | `/api/teams/apikeys/` | List API keys for tenant (IsTenantAdmin) |
| POST | `/api/teams/apikeys/` | Create API key — returns full key once (IsTenantAdmin) |
| DELETE | `/api/teams/apikeys/{id}/` | Revoke API key (IsTenantAdmin) |
| GET/POST | `/api/aliases/` | Alias list + create |
| PATCH | `/api/aliases/{id}/status/` | Enable/disable |
| GET/POST | `/api/forwarding/` | Forwarding rule list + create |
| PATCH | `/api/forwarding/{id}/status/` | Active/paused/disabled |
| GET | `/api/webmail/sso/?mailbox_id=` | One-time SSO token (60s TTL, Redis) |
| POST | `/api/internal/smtp/inbound/` | Postfix inbound policy (INTERNAL_API_SECRET) |
| POST | `/api/internal/smtp/outbound/` | Postfix outbound policy + rate limits |
| GET | `/api/platform/tenants/` | Platform admin: all tenants (IsPlatformAdmin) |
| POST | `/api/platform/tenants/{id}/suspend/` | Suspend tenant |
| POST | `/api/platform/tenants/{id}/activate/` | Activate tenant |
| GET | `/api/backups/` | List backup jobs (filter: scope, status) — IsTenantAdmin |
| POST | `/api/backups/` | Trigger backup job (scope: workspace/domain/mailbox) |
| GET | `/api/backups/{id}/` | Backup job detail + restore_metadata |

### Frontend pages
| Route | File | Purpose |
|-------|------|---------|
| `/app` | `app/(app)/page.tsx` | Dashboard — live stats, workspace status badge |
| `/app/domains` | `app/(app)/domains/page.tsx` | Domain list |
| `/app/domains/[id]` | `app/(app)/domains/[id]/page.tsx` | Domain detail + DNS health + mail engine panel |
| `/app/mailboxes` | `app/(app)/mailboxes/page.tsx` | Mailbox list + create form |
| `/app/mailboxes/[id]` | `app/(app)/mailboxes/[id]/page.tsx` | Mailbox detail, status toggle, webmail SSO, reprovision |
| `/app/billing` | `app/(app)/billing/page.tsx` | Plan card, trial countdown, usage bars (domains/mailboxes/members), feature inclusions |
| `/app/logs` | `app/(app)/logs/page.tsx` | Paginated audit log — event type + result filters, source search, pagination |
| `/app/queue` | `app/(app)/queue/page.tsx` | Mail queue — status filter, cancel action, refresh |
| `/app/spam` | `app/(app)/spam/page.tsx` | Spam quarantine — release to inbox, delete, sender filter |
| `/app/aliases` | `app/(app)/aliases/page.tsx` | Alias list + create (internal or external dest) |
| `/app/forwarding` | `app/(app)/forwarding/page.tsx` | Forwarding rules + keep_copy toggle |
| `/app/settings` | `app/(app)/settings/page.tsx` | Workspace rename, info panel |
| `/app/team` | `app/(app)/team/page.tsx` | Member list, add, role change, remove |
| `/app/onboarding` | `app/(app)/onboarding/page.tsx` | 6-step setup wizard |
| `/app/settings/api-keys` | `app/(app)/settings/api-keys/page.tsx` | API key create/revoke + one-time reveal |
| `/accept-invite` | `app/(auth)/accept-invite/page.tsx` | Token-based invite accept (public, handles auth redirect) |
| `/app/backups` | `app/(app)/backups/page.tsx` | Backup job list: stats cards, scope/status filters, job table with expand detail, auto-poll while jobs are active, trigger new backup |

---

## Billing architecture (Phase 11 — no Stripe)

Plans: Trial (free, 30 days), Starter ($9), Business ($29), Infrastructure ($99). All seeded via data migration `billing/migrations/0003_seed_plans.py`.

- Every new signup auto-creates a `Subscription(status=TRIALING, trial_ends_at=now+30d)` with the Trial plan
- `billing/utils.py` — `check_domain_limit(tenant)` and `check_mailbox_limit(tenant)` return `(bool, str)`, called at top of domain/mailbox POST views; return HTTP 402 on over-limit
- `billing/tasks.py` — `expire_trials` Celery task runs daily at 03:00 UTC; sets TRIALING→PAST_DUE when `trial_ends_at` has passed
- Platform admin can assign any plan via `POST /api/platform/tenants/{id}/plan/` with `{"plan_tier": "business"}` or extend trial with `{"extend_trial_days": 30}`
- Stripe fields (`stripe_customer_id`, `stripe_subscription_id`) are on the Subscription model but blank — reserved for future integration
- `Plan.max_members` added in migration `0002_plan_trial_max_members`; also enforced via `check_member_limit()` in billing/utils.py

---

## Forwarding architecture (Phase 9 — important design decision)

Forwarding rules are synced to mailcow as aliases where `address = mailbox.email` and `goto = comma-separated destinations`. Multiple rules for the same mailbox are merged into one alias. Key behaviors:
- `provision_forwarding(rule)` queries ALL active rules for the mailbox and builds the full goto string
- `delete_forwarding(rule)` is called BEFORE the DB delete so it can see remaining rules
- If `keep_copy=True` on any active rule, the mailbox email itself is included in the goto
- If zero active rules remain after deletion, the alias is deleted from mailcow entirely

---

## Alias architecture (Phase 9)

- `source_address` must not conflict with an existing mailbox email
- Destination is either `destination_mailbox` (FK) or `destination_address` (plain email) — only one set
- Enable/disable: fetches mailcow alias ID via `GET /api/v1/get/alias/{address}`, then edits
- `_sanitize()` in mailcow_adapter strips internal tool names from all error messages stored in DB

---

## Mailcow leakage protection (Phase 7)

`_sanitize()` in `mailcow_adapter.py` strips tool names from all error messages before storing them in `mail_engine_error`. Network exceptions return generic `"Mail service connection error."` to hide internal URL. Terms replaced: mailcow→"the mail server", Postfix→"SMTP", Dovecot→"IMAP", Rspamd→"spam filter", MariaDB/MySQL→"the database".

---

## Rate limiting (Phase 7)

`apps/smtp_policy/rate_limits.py` — `MailRateLimiter`:
- Mailbox: 100 messages/hour
- Domain: 500 messages/hour  
- Tenant: 2000 messages/day
- Check ALL scopes before incrementing ANY (atomic pre-flight check)
- Return DEFER (not REJECT) on rate limit so SMTP client retries

---

## CRITICAL REMINDER — Phase 15 mailcow config changes

**MUST be done before go-live. User explicitly asked to be reminded:**
1. Set `MAILCOW_HOSTNAME=mx.matemail.online` in mailcow `.env`
2. Set `smtpd_banner = mx.matemail.online ESMTP` in Postfix config
3. Set `login_greeting = MateMail IMAP ready` in Dovecot config

These prevent mailcow internal tool names from leaking in SMTP/IMAP banners.

---

## Known IDE issues (Pylance false positives — do NOT fix)

- `ModelSerializer.Meta` override warnings — known DRF/Pylance incompatibility, Django runtime unaffected
- `for_tenant` not found on Manager — Pylance can't see custom manager methods, Django runtime fine
- `domains`, `mailboxes`, `memberships` not found on Tenant — Pylance can't see Django reverse FK relations

`manage.py check` always passes cleanly. These are IDE-only issues.

---

## Pending phases

| Phase | Title | Notes |
|-------|-------|-------|
| 16 | Testing and quality | Integration tests, coverage |
| 17 | Final polish | UI redesign, brand cleanup, "YourBrand" → "MateMail" in prototype |

---

## Infrastructure notes

- VPS: Contabo, IP `176.57.188.13`, AS51167, France
- Domain: matemail.online
- All 60+ MXToolbox blacklists clean (verified 2026-05-30)
- AbuseIPDB: 0% confidence, 3 historical reports (not actionable)
- IP warmup required: 4–8 weeks gradual increase before full send volume
- Postfix policy daemon bridge script (translates Postfix text protocol → HTTP calls to `/api/internal/smtp/`) deferred to Phase 15
