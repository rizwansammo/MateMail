# PROJECT_STATUS.md

**Product:** MateMail  
**Domain:** matemail.online  
**Last updated:** 2026-05-30  
**Current phase:** PHASE 15 — COMPLETE  
**Next phase:** PHASE 16 — Testing and quality

---

## Repository State

| Item | Status |
|------|--------|
| Frontend (Next.js 16, TypeScript, Tailwind v4) | ✅ Phase 1 |
| Backend Django project + split settings | ✅ Phase 1 |
| Docker Compose (MateMail + mail engine stubs) | ✅ Phase 1 |
| Health endpoints `/api/health/*` | ✅ Phase 1 |
| Custom User model + migration | ✅ Phase 1 |
| All 16 Django apps created | ✅ Phase 2 |
| All core models + migrations | ✅ Phase 2 |
| TenantScopedManager | ✅ Phase 2 |
| TenantMiddleware | ✅ Phase 2 |
| RBAC permission classes | ✅ Phase 2 |
| Django admin for all models | ✅ Phase 2 |
| Tenant isolation tests (15/15) | ✅ Phase 2 |
| Mail engine adapter interface | ✅ Phase 1 |
| Auth APIs (signup/login/logout/2FA/reset) | ✅ Phase 3 |
| JWT with tenant_id claim | ✅ Phase 3 |
| Token blacklist (logout) | ✅ Phase 3 |
| Email verification flow | ✅ Phase 3 |
| Password reset flow | ✅ Phase 3 |
| 2FA TOTP + backup codes | ✅ Phase 3 |
| Frontend auth pages (login/signup/2fa/verify/reset) | ✅ Phase 3 |
| AuthContext + token management | ✅ Phase 3 |
| Workspace APIs (list/create/switch/detail) | ✅ Phase 4 |
| Domain basic CRUD API | ✅ Phase 4 |
| Mailbox basic CRUD API | ✅ Phase 4 |
| Onboarding status endpoint | ✅ Phase 4 |
| Protected app layout (sidebar nav) | ✅ Phase 4 |
| Dashboard placeholder | ✅ Phase 4 |
| Onboarding wizard (6-step) | ✅ Phase 4 |
| DKIM key pair generation (2048-bit RSA) | ✅ Phase 5 |
| Real DNS record checking (dnspython) | ✅ Phase 5 |
| DNS health score (0–100, 25 pts/record) | ✅ Phase 5 |
| Domain status auto-update (active on MX+SPF) | ✅ Phase 5 |
| Celery beat schedule (DNS check every 15 min) | ✅ Phase 5 |
| DNS record check API endpoints | ✅ Phase 5 |
| Frontend domain list + detail pages | ✅ Phase 5 |
| Onboarding wizard real DNS check | ✅ Phase 5 |
| MailcowAdapter (full REST API impl) | ✅ Phase 6 |
| StubAdapter (no-op for local dev) | ✅ Phase 6 |
| Adapter factory + MAIL_ENGINE_ADAPTER setting | ✅ Phase 6 |
| Domain provisioning (async Celery + DKIM push) | ✅ Phase 6 |
| Mailbox provisioning (sync, password never stored) | ✅ Phase 6 |
| Domain deprovision on delete | ✅ Phase 6 |
| Mailbox deprovision on delete | ✅ Phase 6 |
| Re-provision endpoints (domain + mailbox) | ✅ Phase 6 |
| Frontend mailbox list page | ✅ Phase 6 |
| Domain detail — mail engine status panel | ✅ Phase 6 |
| SMTP inbound policy endpoint (domain/mailbox/tenant checks) | ✅ Phase 7 |
| SMTP outbound policy endpoint (sender=auth, rate limits, suspension) | ✅ Phase 7 |
| Redis rate limiter (100/h mailbox, 500/h domain, 2000/day tenant) | ✅ Phase 7 |
| Webmail SSO token generation (GET /api/webmail/sso/) | ✅ Phase 7 |
| Webmail SSO token validation (POST /api/internal/webmail/validate-token/) | ✅ Phase 7 |
| Mailbox enable/disable (PATCH /api/mailboxes/{id}/status/ + mail engine sync) | ✅ Phase 7 |
| Frontend mailbox detail page (status toggle, webmail button, re-provision) | ✅ Phase 7 |
| Mailcow leakage fix (sanitize mail_engine_error, generic connection errors) | ✅ Phase 7 |
| INTERNAL_API_SECRET for internal endpoint security | ✅ Phase 7 |
| Workspace stats endpoint (GET /api/workspaces/{id}/stats/) | ✅ Phase 8 |
| Member management (list / add / change role / remove) | ✅ Phase 8 |
| Platform admin API (tenant list / suspend / activate) | ✅ Phase 8 |
| Dashboard overview page — live stats | ✅ Phase 8 |
| Settings page (workspace rename, info panel) | ✅ Phase 8 |
| Team page (member list, add, role change, remove) | ✅ Phase 8 |
| Aliases (CRUD, enable/disable, mail engine sync) | ✅ Phase 9 |
| Forwarding rules (CRUD, pause/disable, multi-dest sync) | ✅ Phase 9 |
| MailcowAdapter: provision_alias, delete_alias, update_alias_active | ✅ Phase 9 |
| MailcowAdapter: provision_forwarding, delete_forwarding (full sync) | ✅ Phase 9 |
| Frontend aliases page (create, toggle, delete) | ✅ Phase 9 |
| Frontend forwarding page (create, toggle, delete) | ✅ Phase 9 |
| MailLog model + audit trail API (GET /api/logs/) | ✅ Phase 10 |
| log_event() utility + wired to domain/mailbox/tenant events | ✅ Phase 10 |
| Mail queue API (GET /api/queue/, POST cancel) | ✅ Phase 10 |
| Quarantine API (GET /api/quarantine/, POST release, DELETE) | ✅ Phase 10 |
| Adapter: cancel_queue_message, release_quarantine_item | ✅ Phase 10 |
| Frontend: /app/logs — paginated audit log with filters | ✅ Phase 10 |
| Frontend: /app/queue — mail queue with cancel action | ✅ Phase 10 |
| Frontend: /app/spam — quarantine with release/delete | ✅ Phase 10 |
| Plan model: Trial/Starter/Business/Infrastructure tiers + max_members | ✅ Phase 11 |
| Data migration: 4 plan records seeded | ✅ Phase 11 |
| Trial subscription auto-created at signup (30 days) | ✅ Phase 11 |
| Plan limit enforcement on domain + mailbox create (402 on over-limit) | ✅ Phase 11 |
| Celery beat: expire_trials task (daily at 03:00 UTC) | ✅ Phase 11 |
| GET /api/billing/ — subscription + plan + usage + trial_days_left | ✅ Phase 11 |
| POST /api/platform/tenants/{id}/plan/ — assign plan or extend trial | ✅ Phase 11 |
| Frontend: /app/billing — plan card, trial countdown, usage bars, feature list | ✅ Phase 11 |
| TeamInvite model + email invite flow | ✅ Phase 12 |
| APIKey model + create/revoke CRUD | ✅ Phase 12 |
| APIKeyAuthentication DRF class (Bearer mm_…) | ✅ Phase 12 |
| TenantMiddleware: API key tenant resolution | ✅ Phase 12 |
| Frontend: /app/team — pending invites section | ✅ Phase 12 |
| Frontend: /app/settings/api-keys — key management | ✅ Phase 12 |
| Frontend: /accept-invite — token accept page | ✅ Phase 12 |
| GET /api/platform/stats/ — platform overview stats | ✅ Phase 13 |
| GET /api/platform/tenants/{id}/ — full tenant detail | ✅ Phase 13 |
| is_platform_admin exposed in UserProfileSerializer + AuthUser | ✅ Phase 13 |
| Frontend: /admin layout (dark, IsPlatformAdmin guard) | ✅ Phase 13 |
| Frontend: /admin — platform dashboard with stats cards | ✅ Phase 13 |
| Frontend: /admin/tenants — tenant list, search, status filter | ✅ Phase 13 |
| Frontend: /admin/tenants/[id] — full tenant management | ✅ Phase 13 |
| GET /api/backups/ — list backup jobs (scope/status filters) | ✅ Phase 14 |
| POST /api/backups/ — trigger a new backup job | ✅ Phase 14 |
| GET /api/backups/{id}/ — backup job detail + restore_metadata | ✅ Phase 14 |
| Celery task: run_backup_task (pending → running → completed/failed) | ✅ Phase 14 |
| BackupJobSerializer + TriggerBackupSerializer | ✅ Phase 14 |
| Frontend: /app/backups — stats cards, filters, job table, expand detail, auto-poll | ✅ Phase 14 |
| nginx/conf.d/matemail.conf — fix /admin/ conflict, ACME challenge location | ✅ Phase 15 |
| nginx/conf.d/matemail-tls.conf — production TLS (HTTP redirect + HTTPS + HSTS + security headers) | ✅ Phase 15 |
| docker-compose.prod.yml — production overrides (prod settings, no port exposure, TLS nginx, certbot) | ✅ Phase 15 |
| scripts/postfix_policy_bridge.py — asyncio Postfix policy daemon bridge (inbound + outbound) | ✅ Phase 15 |
| scripts/postfix-policy-bridge.service — systemd unit for policy bridge | ✅ Phase 15 |
| scripts/init-letsencrypt.sh — Let's Encrypt certificate bootstrap | ✅ Phase 15 |
| scripts/apply-mailcow-config.sh — 3 mailcow leakage-prevention config changes | ✅ Phase 15 |
| scripts/deploy.sh — VPS deployment automation | ✅ Phase 15 |
| backend/config/urls.py — Django admin renamed to /django-admin/ | ✅ Phase 15 |
| backend/config/settings/prod.py — CONN_MAX_AGE, security headers, structured logging | ✅ Phase 15 |

---

## Phase Completion

| Phase | Title | Status |
|-------|-------|--------|
| 0 | Repo audit and project plan | ✅ Complete |
| 1 | Foundation, Docker, ENV, project structure | ✅ Complete |
| 2 | Django core models and multi-tenancy | ✅ Complete |
| 3 | Auth, signup, email verification, 2FA | ✅ Complete |
| 4 | Workspace onboarding flow | ✅ Complete |
| 5 | DNS verification and DKIM generation | ✅ Complete |
| 6 | Real mail engine integration | ✅ Complete |
| 7 | SMTP send, receive, IMAP read, webmail data flow | ✅ Complete |
| 8 | Admin dashboard and customer control center | ✅ Complete |
| 9 | Aliases and forwarding | ✅ Complete |
| 10 | Spam, quarantine, queue, and logs | ✅ Complete |
| 11 | Billing, plans, limits, tenant suspension | ✅ Complete |
| 12 | Team invites, API keys, APIKey auth | ✅ Complete |
| 13 | Internal platform admin portal | ✅ Complete |
| 14 | Backups and restore visibility | ✅ Complete |
| 15 | Deployment, TLS, DNS, production hardening | ✅ Complete |
| 16 | Testing and quality | Pending |
| 17 | Final polish | Pending |

---

## Phase 6 Summary — Real Mail Engine Integration

### New backend files
| File | Purpose |
|------|---------|
| `apps/mail_engine/mailcow_adapter.py` | Full mailcow REST API adapter — domain/DKIM/mailbox CRUD |
| `apps/mail_engine/stub_adapter.py` | No-op adapter — returns success without contacting mailcow |
| `apps/mail_engine/factory.py` | `get_adapter()` singleton — switches via `MAIL_ENGINE_ADAPTER` setting |
| `apps/mail_engine/tasks.py` | `provision_domain_task`, `deprovision_domain_task`, `deprovision_mailbox_task` |

### Modified backend files
| File | Change |
|------|--------|
| `apps/mail_engine/adapter.py` | `provision_mailbox` signature updated: `(mailbox, password: str = "")` |
| `apps/domains/views.py` | Queues `provision_domain_task` on create; queues `deprovision_domain_task` on delete; adds `DomainProvisionView` |
| `apps/domains/serializers.py` | Added `mail_engine_error` to `DomainSerializer` |
| `apps/domains/urls.py` | Added `<id>/provision/` route |
| `apps/mailboxes/views.py` | Sync provisions mailbox on create (password stays in memory); queues `deprovision_mailbox_task` on delete; adds `MailboxReProvisionView` |
| `apps/mailboxes/serializers.py` | Added `mail_engine_error` to `MailboxSerializer`; added `MailboxReProvisionSerializer` |
| `apps/mailboxes/urls.py` | Added `<id>/reprovision/` route |
| `config/settings/base.py` | Added `MAIL_ENGINE_ADAPTER` setting |
| `.env.example` | Added `MAIL_ENGINE_ADAPTER=stub` |

### New API endpoints
| Method | Endpoint | Notes |
|--------|----------|-------|
| POST | `/api/domains/{id}/provision/` | Re-queue mail engine provisioning for a domain |
| POST | `/api/mailboxes/{id}/reprovision/` | Re-provision mailbox with new password (IsTenantAdmin) |

### New frontend files
| File | Purpose |
|------|---------|
| `app/(app)/mailboxes/page.tsx` | Mailbox list: status, provisioning badge, quota, add-mailbox form |

### Modified frontend files
| File | Change |
|------|--------|
| `app/(app)/domains/[id]/page.tsx` | Added mail engine provisioning status panel + error display + retry button |

### Adapter design
- **`MAIL_ENGINE_ADAPTER=stub`** (default): `StubAdapter` — logs all calls, returns success. Use locally without mailcow.
- **`MAIL_ENGINE_ADAPTER=mailcow`**: `MailcowAdapter` — calls mailcow REST API with `X-API-Key` auth.
- Factory is a thread-safe singleton: `get_adapter()` — import and call anywhere.

### Provisioning rules
| Operation | Method | Password handling |
|-----------|--------|------------------|
| Domain create | Async Celery (provision_domain_task) | No password involved |
| Domain delete | Async Celery (deprovision_domain_task) | No password involved |
| Mailbox create | **Synchronous** in API call | Password in memory only — never stored, never queued |
| Mailbox delete | Async Celery (deprovision_mailbox_task) | No password involved |
| Mailbox reprovision | **Synchronous** via `POST /api/mailboxes/{id}/reprovision/` | Password in memory only |

---

## Quick Start (Phase 6 complete)

```bash
cd backend

# No new migrations needed for Phase 6 (model changes were in Phase 5)
python manage.py check --settings=config.settings.dev

# Run tenant isolation tests
DATABASE_URL="sqlite:///./test_db.sqlite3" python manage.py test tests.test_tenant_isolation --settings=config.settings.dev

# Start Celery worker (for domain provisioning tasks)
celery -A config worker -l info

# With Docker:
docker compose up -d postgres redis
docker compose run --rm backend python manage.py migrate

# To use real mailcow, set in .env:
# MAIL_ENGINE_ADAPTER=mailcow
# MAIL_ENGINE_API_URL=http://mailcow-nginx:80
# MAIL_ENGINE_API_KEY=<your-mailcow-api-key>
```

---

## Phase 7 Notes (coming up)

- **SMTP submission**: Accept authenticated SMTP from webmail/clients — enforce sender = mailbox owner
- **Inbound routing**: Postfix policy to accept only for verified active domains
- **IMAP access**: Dovecot authentication via MateMail credentials
- **Webmail data flow**: Bridge MateMail auth tokens → Roundcube/SOGo session
- **Sending limits**: Per-tenant, per-domain, per-mailbox rate limits via Redis counters
- **Suspended tenants**: Hook into billing/suspension to block sending
- Security rules: Never build an open relay; outbound only for authenticated active mailboxes
