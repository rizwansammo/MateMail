# PROJECT_STATUS.md

**Product:** MateMail  
**Owner:** NetaMate Solutions  
**Domain:** matemail.online  
**Last updated:** 2026-09-10  
**Current phase:** PRODUCTION READINESS PHASE 0 — COMPLETE  
**Next phase:** to be assigned

---

## Architecture direction

As of **DEC-011 (2026-09-10)**, MateMail is one integrated business email
platform. Postfix / Dovecot / Rspamd — orchestrated via mailcow — are MateMail's
**internal Mail Engine**, an implementation detail customers must never see.
MateMail owns tenancy, permissions, plans, onboarding, provisioning, abuse
policy, audit, backups, monitoring, webmail and all UI.

See `docs/ARCHITECTURE.md` v2.0, and DEC-011 / DEC-005r / DEC-007r in
`docs/DECISIONS.md`. The previous framing (control panel beside a separately
presented mailcow) is superseded.

---

## Honest readiness summary

The feature phases below (1–15) describe what was *built*. A production-readiness
audit on 2026-09-10 found that several of them shipped an API and a UI page
without the mechanism underneath. Read this section before trusting the tables.

**Not production ready.** Blocking items, in order:

| # | Blocker | State |
|---|---------|-------|
| 1 | **No Mail Engine.** `docker-compose.mailengine.yml` is an nginx placeholder. The engine was never installed, so no mail has ever been sent or received. `MailcowAdapter` is written but has nothing to call. | Open |
| 2 | **Outbound SMTP policy never consulted.** The documented Postfix restriction order puts `permit_sasl_authenticated` before `check_policy_service`, so rate limits, sender-equals-auth and tenant suspension are not enforced for sending. | Open |
| 3 | **Backups are simulated.** `run_backup_task` counts rows, invents a size and marks the job complete. Nothing is backed up and there is no restore path. Domain deletion cascades to permanent mail destruction. | Open |
| 4 | **No webmail.** No inbox/compose/thread routes exist. The SSO bridge cannot authenticate anyone, because webmail needs the mailbox IMAP password and MateMail deliberately never stores it. Direction fixed by DEC-005r (MateMail-built, not SOGo); auth mechanism still open as TBD-G. | Open |
| 5 | **Queue, quarantine and storage usage are empty shells.** Nothing writes `QueueMessage` or `QuarantineMessage`; `storage_used_mb` is never assigned. No mailcow→MateMail sync task exists. | Open |
| 6 | **Domain ownership is not verified** before a domain is provisioned into the mail engine. | Open |
| 7 | **Deployment architecture is contradictory** and does not yet match the NetaMate model in `CLAUDE.md`: ports must move to 8020/3020 (8015/3015 belong to MateConnect), CI must build and push GHCR images instead of building on the VPS, and the dead containerized-nginx configs must go. | Open |
| 8 | **Engine detail can reach customers.** The error sanitizer is case-sensitive and incomplete, and one code path writes raw exceptions into a customer-visible field. Violates DEC-011. See the Mail Engine integration review. | Open |

Resolved in Phase 0: the four critical application-security defects and the
broken audit log. See the Phase 0 section below.

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
| Tenant isolation tests | ✅ Phase 2 (15 ORM tests; 2 were failing until Phase 0 — see below) |
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
| 16 | Testing and quality | Partial — Phase 0 added 92 tests and a green suite; broader coverage still pending |
| 17 | Final polish | Pending |

### Production readiness phases

| Phase | Title | Status |
|-------|-------|--------|
| P0 | Trustworthy baseline + critical application security | ✅ Complete (2026-09-10) |

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

---

## Production Readiness Phase 0 — Trustworthy baseline + critical app security

**Completed 2026-09-10.** Scope: establish a verifiable baseline and fix the
critical application-security defects. No deployment, no mail engine work.

### Baseline established

| Check | Before Phase 0 | After |
|-------|----------------|-------|
| `pip install -r requirements.txt` (clean venv) | pass | pass |
| `pip install -r requirements-dev.txt` (clean venv) | pass | pass |
| `manage.py check` (dev) | pass | pass |
| `manage.py check --deploy` (prod) | 1 warning (`W019`) | 1 warning (`W019`) |
| `manage.py makemigrations --check --dry-run` | **8 pending operations on `logs`** | no changes detected |
| Backend test suite | **15 tests, 2 errors** | **107 tests, 0 failures** |
| `npm ci` | pass | pass |
| `npm run lint` | 18 errors, 19 warnings | 18 errors, 19 warnings (unchanged; pre-existing `react-hooks` issues) |
| `npm run build` | pass, 27 routes | pass, 27 routes |

`W019` is `X_FRAME_OPTIONS = "SAMEORIGIN"` rather than `"DENY"`, kept deliberately
so the app can frame its own pages. Revisit if that need disappears.

### Security defects fixed

| ID | Defect | Fix |
|----|--------|-----|
| C1 | **2FA fully bypassable.** `partial_token` was a real `AccessToken`; DRF accepted it, so a password alone granted full API access. Reproduced before the fix. | Opaque single-use Redis-backed challenge (`apps/accounts/challenge.py`); `make_partial_token` deleted |
| C2 | **Admin disclosed DKIM private keys and TOTP secrets.** `readonly_fields` without a field restriction renders every editable field. | `exclude` on all secret-bearing admins; `has_dkim_private_key` boolean replaces the key |
| C3 | **`read_only` members could mutate.** Ten endpoints guarded only by `HasTenantAccess`, which ignores role — a read-only member could delete a domain or forward a mailbox externally. | `TenantReadAdminWrite` / `TenantReadSupportWrite`; full matrix test |
| C4 | **Audit log silently recorded nothing.** Phase 10 dropped `MailLog.Meta.db_table`, repointing the ORM at a non-existent table; `log_event` swallowed the error. | `db_table` restored + migration `logs/0002`; broad `except` removed |
| H1 | Internal webmail validator routed publicly at `/api/webmail/validate-token/`, outside the nginx-denied prefix | moved to `/api/internal/webmail/validate-token/` |
| H4 | Password reset left refresh tokens and other reset links valid | all outstanding tokens blacklisted; sibling reset tokens burned; challenge bound to password hash |
| H5 | Inactive user's valid JWT caused an unhandled middleware **500** | `AuthenticationFailed` caught → 401 |
| H6 | Internal secret compared with `==` (timing oracle) | `hmac.compare_digest` |
| H2 | Email verification never enforced | `IsEmailVerified` on domain + mailbox provisioning |

### Day-one defects fixed

- `/api/platform/tenants/{id}/` raised `FieldError` (queried `Domain.name` / `Domain.created_at`, which do not exist) — the whole `/admin/tenants/[id]` page was dead.
- Sidebar linked to `/app/dns-health`, a route that has never existed — link removed.
- Domain normalization used `str.lstrip("www.")`, which strips a character set, not a prefix (`web.example.com` → `eb.example.com`) — replaced with `removeprefix` plus hostname validation.
- `billing/utils.days_left_on_trial` imported `billing.models` instead of `apps.billing.models`.

### Tests added (92 new, 107 total)

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_two_factor.py` | 10 | challenge cannot authenticate; valid 2FA works; single-use; attempt cap; password-change voids |
| `tests/test_role_permissions.py` | 16 | every mutating endpoint × all four roles |
| `tests/test_cross_tenant_api.py` | 16 | HTTP-layer isolation: read, mutate, delete, cross-tenant parent attachment |
| `tests/test_day_one_defects.py` | 16 | audit-log writes, platform-admin detail, domain normalization, billing import |
| `tests/test_auth_security.py` | 11 | reset revokes sessions/tokens; inactive user → 401 not 500 |
| `tests/test_email_verification.py` | 8 | unverified cannot provision; reads still work; verification unblocks |
| `tests/test_internal_endpoints.py` | 8 | internal routes under `/api/internal/`; secret enforcement; constant-time compare |
| `tests/factories.py` | — | shared helpers (no secrets; test-only fast password hasher) |

### Known limitations carried forward

- Access tokens (15 min) cannot be revoked mid-life; password reset revokes refresh tokens only.
- The 2FA challenge requires a working cache. Production must have `CACHES` pointed at Redis (now configured in `config/settings/base.py`).
- `IsEmailVerified` blocks provisioning for any account with `email_verified = False`, which includes accounts created before this change.
- Frontend lint carries 18 pre-existing `react-hooks` errors, untouched by Phase 0.
- **MateMail holds DKIM private keys it should not hold.** Plaintext in
  PostgreSQL, pushed over plaintext HTTP. Compromise allows forging mail as any
  customer domain. P0 removed it from Django admin; full remediation is the
  DEC-007r migration in P4.

---

## Mail Engine integration review (2026-09-10)

Assessed against DEC-011: MateMail owns the product; the engine is internal and
replaceable. No code changed as part of this review.

### Coverage — what the adapter already wraps

19 methods, all reached only via `get_adapter()`. No module outside
`apps/mail_engine/` calls the engine, and no engine branding appears in any
frontend file. That boundary is genuinely in place.

| Capability | Adapter | MateMail owns policy | Notes |
|---|---|---|---|
| Domain create / suspend / delete | ✅ | ✅ | engine per-domain limits hardcoded, not plan-derived |
| Mailbox create / enable / disable / delete | ✅ | ✅ | provisioning is synchronous in the request path |
| Mailbox password / quota update | ✅ | ✅ | quota has no plan ceiling |
| Alias create / delete / toggle | ✅ | ✅ | |
| Forwarding create / delete | ✅ | ⚠️ | rule-set logic lives **inside** the adapter — see L4 |
| DKIM public key read | ✅ | ✅ | **Wrong ownership today:** Django generates and stores the private key. DEC-007r puts key generation and storage in the engine. See L9. |
| Queue + quarantine read / act | ✅ | ❌ | engine-global, no tenant dimension; nothing calls them |
| Health ping | ✅ | — | boolean only |

### Leaks — engine detail reaching or able to reach customers

| ID | Leak | Severity |
|----|------|----------|
| L1 | `apps/mailboxes/views.py:73` writes `str(exc)[:500]` straight into `mail_engine_error`, which `MailboxSerializer` exposes. A connection failure puts the internal engine hostname and port in the customer UI. **Unsanitized.** | High |
| L2 | `_sanitize()` is case-sensitive and term-limited. Verified: `Mailcow`, `MAILCOW`, `postfix`, `dovecot`, `SOGo`, `Roundcube`, `ClamAV` and engine API paths all pass through unchanged — 7 of 11 realistic probes leaked. Even a matched case leaves structure like `HTTPConnectionPool(host='…-nginx', port=8080)`. | High |
| L3 | `ProvisionResult.raw` carries the engine's response dict through the port. Not currently serialized to customers, but nothing prevents it. | Medium |
| L4 | `provision_forwarding` / `delete_forwarding` query `apps.forwarding.models` and implement `keep_copy` and active-rule-set semantics **inside the adapter**. This is MateMail product logic below the port: a replacement engine would have to reimplement it. | Medium |
| L5 | Adapter methods take Django model instances, coupling the port to the ORM. `tasks.py` already works around this with `_FakeDomain` / `_FakeMailbox` shims. | Medium |
| L6 | `WEBMAIL_BASE_URL` points wherever webmail lives; if aimed at the engine's SOGo it becomes a customer-facing engine surface. Contradicts DEC-005r. | Medium |
| L7 | `/api/health/` is public and reports `mail_engine` status, disclosing that a distinct mail engine exists and whether it is up. | Low |
| L8 | Docstrings in `smtp_policy/views.py` and `webmail/views.py` name Postfix, Dovecot, Roundcube and SOGo. Internal-only, but should be reframed as Mail Engine components. | Low |
| L9 | **DKIM private key held in the control plane.** Django generates the keypair, stores the PEM unencrypted in `Domain.dkim_private_key`, and pushes it to the engine over plaintext HTTP. Violates DEC-007r: the signing key must be generated and stored inside the engine, with MateMail reading only the public half. | High |

### Missing — capability the integrated product needs and the port lacks

**Blocks correctness or policy:**
- No engine-side rate limit push. MateMail's Redis limiter exists but the engine never consults MateMail on send (the policy path is not wired — blocker #2).
- No `list_domains` / `list_mailboxes` reconciliation, so MateMail cannot detect drift between its records and the engine.
- No typed errors. Everything collapses to `success` + `message`, so callers cannot distinguish "already exists" from "engine down" from "rejected", and cannot retry intelligently.
- No idempotency contract, so a retried provision may behave differently from the first attempt.

**Blocks customer-visible features already in the UI:**
- No `get_mailbox_usage` → `storage_used_mb` is permanently 0 and every usage bar reads zero.
- No last-login read → `Mailbox.last_login` is never set.
- Queue and quarantine reads have no tenant dimension, so the existing pages cannot be populated safely.

**Blocks features a business email product is expected to have:**
- No autoresponder / out-of-office, no sieve or filter rules.
- No catch-all address support.
- No per-domain or per-mailbox spam policy (threshold, allow/block lists).
- No app passwords — needed once web login uses 2FA but IMAP clients still need a credential.
- No `rotate_dkim_key()` on the port, and no way to have the engine generate a keypair and return only the public half — both required by DEC-007r.
- No message-level operations (folders, messages, send), which MateMail webmail will require.
- No Dovecot master-user support, the likely mechanism for webmail auth (TBD-G).
- No MTA-STS / TLS policy per domain.
- No engine log or event stream for the audit trail.
- No backup or restore hooks.

### Boundary rules now recorded in `docs/ARCHITECTURE.md`

1. Only `apps/mail_engine/` may call the engine.
2. No engine-shaped data crosses the port outward.
3. No MateMail product logic below the port.
4. Errors are typed.
5. Customer-visible text never originates from the engine.

L1–L5 are existing violations of rules 2, 3 and 5.

---

## Revised roadmap (post DEC-011)

Authoritative plan. Supersedes the phase tables in `docs/TODO.md`. Ordering rule:
each phase leaves the system more defensible than it found it, and nothing
depends on the Mail Engine until the boundary that hides it is sound.

**P0 — Trustworthy baseline + critical app security.** Complete 2026-09-10.

---

### P1 — Harden the adapter boundary *(no engine required)*

The boundary must be right *before* the engine exists, or every later phase
builds on a leaky port. All of this is testable against `StubAdapter`.

- Fix L1: never write a raw exception into `mail_engine_error`; customer-visible
  text is MateMail-authored only.
- Fix L2: replace term-substitution sanitizing with an allowlist — customers get
  a MateMail message keyed by error type; raw engine text goes to logs only.
- Introduce typed adapter errors (`EngineUnavailable`, `AlreadyExists`,
  `Rejected`, `NotFound`, `QuotaExceeded`) and make callers branch on them.
- Fix L4/L5: adapter takes plain DTOs, not Django models; move forwarding
  rule-set semantics out of the adapter into `apps/forwarding`. Retires the
  `_FakeDomain` / `_FakeMailbox` shims.
- Drop `raw` from the outward contract (L3).
- Add `list_domains` / `list_mailboxes` / `get_mailbox_usage` / `get_last_login`
  to the port, plus an idempotency contract per method.
- Reframe internal docstrings as Mail Engine components (L8).
- Restrict `/api/health/` detail behind the internal secret (L7).
- Tests: a leak test asserting no engine term can reach a serializer; one shared
  contract test suite both adapters must satisfy.

**Exit:** both adapters pass one shared contract suite; no engine string can
reach a customer-visible field; suite green.

---

### P2 — Align deployment with the NetaMate model *(no engine, no VPS changes)*

- Move to `127.0.0.1:8020` (backend) and `127.0.0.1:3020` (frontend).
- Rewrite CI: build images in GitHub Actions, push to
  `ghcr.io/rizwansammo/matemail-{backend,frontend}:<sha>`, deploy by
  `docker compose pull && up -d`. No source build on the VPS, no Git checkout.
- Celery worker and beat reuse the backend image.
- Delete the dead containerized-nginx/certbot configs; host-native nginx is the
  single authority. Carry the security headers and `/api/internal/` deny rule
  into the host vhost, and add a CSP.
- Remove the `./backend:/app` bind mount from the production compose path.
- Compose hygiene: no host ports on postgres/redis, named volumes,
  `restart: unless-stopped`, healthchecks on every service.
- Carry P0's CI gate into the new pipeline: tests, `makemigrations --check`,
  frontend build and lint must pass before deploy.
- Target config layout `/opt/MateMail/`.

**Exit:** a dry-run deploy to a scratch target succeeds from GHCR images alone;
port map documented and free of collisions with existing MateServer apps.

---

### P3 — Domain ownership + abuse prevention *(no engine required)*

Must land before any real domain is provisioned into a real engine.

- `_matemail-verify.<domain>` TXT token required before provisioning; scope
  uniqueness per-tenant until verified (closes blocker 6).
- Cap workspaces per user; make plan `max_members` actually enforced.
- Implement the `SECURITY.md` rate-limit table: signup, forgot-password, DNS
  check, mailbox create; per-account login lockout; strict per-user 2FA throttle;
  single-use TOTP codes. Add `django-ratelimit`.
- API key scopes; default read-only; refuse platform-admin inheritance.
- Make the DNS check endpoint async and rate-limited; fan out the periodic sweep.
- Move refresh tokens to `httpOnly` cookies.
- Route transactional mail via an external provider on a subdomain — MateMail
  must not depend on the engine it is bootstrapping.
- Interim mitigation only: encrypt `Domain.dkim_private_key` at rest and keep it
  out of logs and serializers. This is a stopgap — DEC-007r removes the column
  entirely in P4, so do not build anything new on it.

**Exit:** a scripted signup-and-abuse attempt is throttled at every stage; an
unverified domain cannot be provisioned.

---

### P4 — Stand up the Mail Engine *(first engine phase)*

Only now is it safe to deploy the engine.

- Confirm MateServer capacity: the engine needs roughly 6 GB RAM plus about
  2 GB for the app stack. Verify headroom against existing NetaMate apps first.
- **Verify outbound TCP/25 from MateServer itself** before deploying the engine.
  Contabo does not restrict ports or outbound traffic by default, so treat this
  as a connectivity check, not a support request. Only contact Contabo if the
  real test from the actual server shows 25 is blocked.
- Note Contabo's sending policy of roughly **25 emails/minute** and size the
  launch plan and outbound rate limits against it — this is a throughput
  constraint on the platform, not a blocker.
- Set reverse DNS for the send IP to `mx.matemail.online`; publish agreeing
  forward and reverse records.
- Install the engine as its own Compose project; join it to the app network so
  the backend reaches it internally.
- Resolve the 80/443 conflict: host nginx owns them, so the engine nginx must not
  claim them. The engine admin UI is never published — localhost/VPN only.
- Derive the engine per-domain limits from the tenant `Plan` instead of the
  hardcoded values; add a `max_quota_mb` ceiling on mailbox creation.
- Publish MateMail's own SPF, DKIM and DMARC records.
- **Migrate DKIM key ownership (DEC-007r).** Move keypair generation into the
  engine; add `rotate_dkim_key()` to the port; have MateMail read only the public
  key. Backfill existing domains one at a time, sequenced with each customer's
  DNS update because regeneration changes the published record — never as a bulk
  job. Then purge and drop `Domain.dkim_private_key`.
- Make mailbox provisioning asynchronous so a slow engine cannot exhaust workers.
- Ship the interim **Email Clients** page (per-mailbox IMAP/SMTP settings with
  Outlook / Apple Mail / Thunderbird guides) so the product is usable now.

**Exit:** a mailbox created in the MateMail UI sends to Gmail and receives a
reply; mail-tester scores 9/10 or better; the engine admin UI is verified
unreachable from the internet.

---

### P5 — Make mail policy actually enforce

Closes blocker 2 — the phase that protects sending reputation.

- Fix the Postfix restriction order so the policy service runs *before*
  `permit_sasl_authenticated`, or move it to `smtpd_end_of_data_restrictions`
  where a message is counted once rather than once per recipient.
- Make the policy bridge reachable: bind to the container-visible address,
  correct `DJANGO_INTERNAL_URL` to the new port, source-restrict the internal
  nginx location instead of denying it wholesale.
- Write the missing `scripts/install-policy-bridge.sh` (`deploy.sh` already
  instructs operators to run it).
- Teach the inbound policy about aliases, forwarding rules and catch-alls, or
  every alias address will bounce.
- Make the rate limiter atomic; count per message; read limits from the plan.
- Tenant suspension must call `suspend_domain` so it stops outbound too.
- Require destination confirmation before a forwarding rule activates; notify
  the mailbox owner and the workspace owner.

**Exit:** every box in the `SECURITY.md` no-open-relay checklist is ticked by an
automated test that speaks real SMTP.

---

### P6 — Backups, restore and safe deletion

Closes blocker 3.

- Soft-delete domains and mailboxes with a 30-day hold before any engine
  deletion; typed confirmation in the UI.
- Real backups: nightly `pg_dump` plus mail-store sync to encrypted offsite
  storage, with retention.
- Rewrite `run_backup_task` to report real artifacts — or remove the Backups page
  until it does. A false green is worse than a missing feature.
- Restore runbook, performed at least once from a wiped environment.
- Daily verification that the previous night's backup exists and is plausibly sized.

**Exit:** a tenant's mail has been restored from offsite into a clean
environment, timed and documented. No UI claims a backup that does not exist.

---

### P7 — Own the operational surface

Closes blocker 5 and removes the operator's need for the engine admin UI.

- Engine to MateMail sync task: queue, quarantine, per-mailbox usage, last login,
  each mapped to a tenant on the way in.
- Reconciliation pass flagging MateMail/engine disagreement, surfaced in
  platform admin.
- Real pagination and the documented filters on `/api/logs/`; retention policy.
- Per-domain and per-mailbox spam policy through MateMail's own API.
- Platform-admin coverage for every routine operational task.
- Monitoring: error tracking, uptime checks on app and mail ports, disk alerts on
  the mail volume, blocklist monitoring, outbound-volume anomaly alerts.

**Exit:** queue, quarantine and storage reflect real engine state within five
minutes; an operator can run a normal week without touching the engine directly.

---

### P8 — MateMail webmail

DEC-005r: MateMail-built, not SOGo.

- **Resolve TBD-G first:** the auth mechanism. Likely a Dovecot master user, so a
  verified MateMail session exchanges for an authenticated IMAP session. Record
  the decision before writing code.
- Resolve TBD-H: app passwords for IMAP/SMTP clients once web login uses 2FA.
- Add message-level operations to the port: folders, messages, send, flags.
- Build inbox, reading pane, compose, thread view and webmail settings.
- Serve `webmail.matemail.online` from MateMail with its own vhost and
  certificate — `WEBMAIL_BASE_URL` currently points at a host no config serves (L6).

**Exit:** a customer reads and sends mail through MateMail's own interface, and
no engine-supplied UI is reachable by customers.

---

### P9 — Launch readiness

- Broaden test coverage past P0's security core.
- Public front door: landing, pricing, security and docs pages. Today `/`
  redirects straight to `/login`, so there is nowhere to send a prospect.
- Billing: no payment path exists — plans have prices but no way to pay.
- Runbooks: blocklist removal, restore, engine upgrade, incident response.
- Soft launch to 5–10 friendly tenants on real domains; watch for two weeks
  before opening signups.

**Exit:** ten real mailboxes sending and receiving without complaint; alerting
proven by a drill.

---

### Sequencing notes

- **P1 to P3 need no engine and no VPS access.** That is deliberate: the
  boundary, the pipeline and the abuse controls should all be sound before real
  mail flows.
- **Verify outbound TCP/25 from MateServer early** (during P1 is ideal) so any
  surprise is found before P4 depends on it. It is a check, not a request:
  Contabo does not block it by default. Escalate only if the live test fails.
- **Contabo's ~25 emails/minute sending policy is a planning input**, not a
  deployment blocker. It bounds onboarding pace and per-tenant send rates, so
  factor it into the plan-tier limits set in P4 and the launch sizing in P9.
- **P8 is the largest phase and the least urgent.** The interim Email Clients
  page in P4 makes the product sellable without it.
- Do not defer P6 past launch. Simulated backups plus one-call irreversible
  deletion is the combination that turns a bad week into a closed business.
