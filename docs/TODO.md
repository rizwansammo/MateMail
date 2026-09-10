# TODO.md — MateMail Task Tracker

**Product:** MateMail
**Last updated:** 2026-09-10
**Current phase:** Production Readiness P0 complete → next phase awaiting assignment

Legend: ✅ Done | 🔄 In Progress | ⬜ Pending | ❌ Blocked

> **This file's phase 1–17 checklists below are the ORIGINAL feature plan and are
> stale in two ways.** They still show Phase 1–2 tasks as pending although that
> work shipped, and they predate DEC-011 (MateMail as one integrated platform).
>
> The authoritative plan is now the **Production Readiness roadmap** in
> `PROJECT_STATUS.md`. The Phase 7 "Webmail Data Flow" checklist below is the
> closest thing to the webmail work still owed, and remains largely unbuilt.
>
> Retained for history and because several unchecked items are still real work.

---

## PHASE 0 — Repo Audit and Project Plan

- ✅ Inspect repository structure
- ✅ Find and read UI prototype (MateMailUI.jsx)
- ✅ Extract all screens and routes from prototype
- ✅ Identify brand placeholders to replace
- ✅ Write PROJECT_STATUS.md
- ✅ Write docs/ARCHITECTURE.md
- ✅ Write docs/PHASES.md
- ✅ Write docs/TODO.md
- ✅ Write docs/DECISIONS.md
- ✅ Write docs/DEPLOYMENT.md
- ✅ Write docs/MAIL_ENGINE.md
- ✅ Write docs/SECURITY.md
- ✅ Choose mail engine (mailcow-style foundation — Option B)

---

## PHASE 1 — Foundation, Docker, ENV, Project Structure

### Frontend
- ⬜ Create `frontend/` with `npx create-next-app`
- ⬜ Configure TypeScript
- ⬜ Install and configure Tailwind CSS
- ⬜ Install Lucide React (matches UI prototype icons)
- ⬜ Create base layout structure
- ⬜ Set up environment variable config
- ⬜ Create basic health page (GET /)

### Backend
- ⬜ Create `backend/` Django project
- ⬜ Install Django, DRF, psycopg2, celery, redis, django-environ
- ⬜ Configure Django settings (base, dev, prod)
- ⬜ Set up URL routing
- ⬜ Add `GET /api/health/` endpoint
- ⬜ Add `GET /api/health/db/` endpoint
- ⬜ Add `GET /api/health/redis/` endpoint
- ⬜ Add `GET /api/health/mail-engine/` endpoint (proxy to mailcow API)
- ⬜ Set up Celery app with Redis broker
- ⬜ Configure CORS for frontend origin

### Infrastructure
- ⬜ Create `docker-compose.yml` (main services)
- ⬜ Create `docker-compose.mailcow.yml` (mail engine services)
- ⬜ Create `.env.example` with all required variables
- ⬜ Add `.gitignore` (secrets, .env, node_modules, __pycache__, etc.)
- ⬜ Add `Dockerfile` for backend
- ⬜ Add `Dockerfile` for frontend
- ⬜ Configure postgres service
- ⬜ Configure redis service
- ⬜ Configure celery-worker service
- ⬜ Configure celery-beat service

### Documentation
- ⬜ Update PROJECT_STATUS.md at phase end
- ⬜ Update TODO.md with Phase 2 tasks

---

## PHASE 2 — Django Core Models and Multi-Tenancy

### Django Apps
- ⬜ Create `accounts` app
- ⬜ Create `tenants` app
- ⬜ Create `domains` app
- ⬜ Create `mailboxes` app
- ⬜ Create `dnshealth` app
- ⬜ Create `aliases` app
- ⬜ Create `forwarding` app
- ⬜ Create `mailqueue` app
- ⬜ Create `logs` app
- ⬜ Create `quarantine` app
- ⬜ Create `backups` app
- ⬜ Create `billing` app
- ⬜ Create `teams` app
- ⬜ Create `platform_admin` app
- ⬜ Create `webmail` app

### Models
- ⬜ Tenant model
- ⬜ User model (custom, email-based)
- ⬜ TenantMembership model
- ⬜ Domain model
- ⬜ Mailbox model
- ⬜ Alias model
- ⬜ ForwardingRule model
- ⬜ DNSRecordCheck model
- ⬜ MailLog model
- ⬜ QueueMessage model
- ⬜ QuarantineMessage model
- ⬜ BackupJob model
- ⬜ Plan model
- ⬜ Subscription model
- ⬜ Invoice model

### Infrastructure
- ⬜ TenantScopedManager for all tenant-owned models
- ⬜ Tenant middleware (resolves request.tenant from JWT)
- ⬜ Migrations for all models
- ⬜ Register all models in Django admin
- ⬜ Write tenant isolation tests (domain, mailbox, alias, log)

---

## PHASE 3 — Auth, Signup, Email Verification, 2FA

### Backend
- ⬜ POST /api/auth/signup/
- ⬜ POST /api/auth/login/ (JWT issue)
- ⬜ POST /api/auth/logout/ (token revoke)
- ⬜ POST /api/auth/forgot-password/
- ⬜ POST /api/auth/reset-password/
- ⬜ POST /api/auth/verify-email/
- ⬜ POST /api/auth/2fa/setup/ (TOTP QR code + secret)
- ⬜ POST /api/auth/2fa/verify/ (confirm TOTP code)
- ⬜ GET /api/me/
- ⬜ GET /api/workspaces/
- ⬜ POST /api/workspaces/switch/
- ⬜ Email sending (verification, reset, invite)
- ⬜ Rate limiting on auth endpoints

### Frontend
- ⬜ Login screen (from prototype)
- ⬜ Signup screen (from prototype)
- ⬜ Forgot password screen
- ⬜ Email verification screen
- ⬜ 2FA setup screen (QR code + backup codes)
- ⬜ API client setup (React Query / axios)
- ⬜ Auth context / token storage

---

## PHASE 4 — Workspace Onboarding Flow

### Backend
- ⬜ POST /api/onboarding/workspace/
- ⬜ POST /api/domains/
- ⬜ GET /api/domains/:id/dns-records/
- ⬜ POST /api/domains/:id/verify/
- ⬜ POST /api/mailboxes/
- ⬜ GET /api/onboarding/status/
- ⬜ DNS record generation logic

### Frontend
- ⬜ Step 1: Workspace setup
- ⬜ Step 2: Add domain
- ⬜ Step 3: DNS setup (copyable cards)
- ⬜ Step 4: DNS verification status
- ⬜ Step 5: Create first mailbox
- ⬜ Step 6: Complete / go to dashboard

---

## PHASE 5 — DNS Verification and DKIM

- ⬜ Install dnspython
- ⬜ MX record verification
- ⬜ SPF TXT record verification
- ⬜ DKIM TXT record verification
- ⬜ DMARC TXT record verification
- ⬜ MTA-STS TXT record verification
- ⬜ TLS-RPT TXT record verification
- ⬜ DNS health score calculation
- ⬜ Store results in DNSRecordCheck
- ⬜ Celery periodic task (every 15 min)
- ⬜ Manual retry endpoint
- ⬜ DKIM provisioning via mailcow API
- ⬜ Tests: passing DNS, missing DNS, wrong value

---

## PHASE 6 — Real Mail Engine Integration

- ⬜ MailEngineAdapter interface
- ⬜ MailcowAdapter implementation
- ⬜ provision_domain()
- ⬜ suspend_domain()
- ⬜ provision_mailbox()
- ⬜ disable_mailbox()
- ⬜ update_mailbox_password()
- ⬜ update_mailbox_quota()
- ⬜ provision_alias()
- ⬜ remove_alias()
- ⬜ provision_forwarding()
- ⬜ remove_forwarding()
- ⬜ sync_mail_engine_state() (reconciliation)
- ⬜ No open relay test
- ⬜ Unknown domain rejection test
- ⬜ Unknown recipient rejection test
- ⬜ Disabled mailbox auth rejection test
- ⬜ Suspended tenant SMTP rejection test

---

## PHASE 7 — Webmail Data Flow

- ⬜ GET /api/webmail/folders/
- ⬜ GET /api/webmail/messages/?folder=inbox
- ⬜ GET /api/webmail/messages/:id/
- ⬜ POST /api/webmail/send/
- ⬜ POST /api/webmail/drafts/
- ⬜ PATCH /api/webmail/messages/:id/
- ⬜ Folder actions (archive, trash, restore, mark-read)
- ⬜ Webmail inbox UI
- ⬜ Reading pane
- ⬜ Compose modal
- ⬜ Thread view
- ⬜ Folder navigation

---

## PHASE 8 — Admin Dashboard (Real Data)

- ⬜ GET /api/dashboard/overview/
- ⬜ Real domain count
- ⬜ Real mailbox count
- ⬜ Mail traffic data from mailcow
- ⬜ Spam blocked count
- ⬜ DNS health score aggregate
- ⬜ Storage used aggregate
- ⬜ Queue size from mailcow
- ⬜ Recent activity log
- ⬜ All admin pages connected to real APIs

---

## PHASE 9-17

*(Detailed tasks will be added as each phase begins)*

- ⬜ Phase 9: Aliases and forwarding
- ⬜ Phase 10: Spam, quarantine, queue, logs
- ⬜ Phase 11: Billing, plans, limits
- ⬜ Phase 12: Team, RBAC, API keys
- ⬜ Phase 13: Platform admin portal
- ⬜ Phase 14: Backups and restore
- ⬜ Phase 15: Production deployment and TLS
- ⬜ Phase 16: Testing and quality
- ⬜ Phase 17: Final polish and brand cleanup

---

## Prototype Brand Replacements Required (Phase 17)

| Current value | Replace with |
|--------------|-------------|
| `YourBrand` | `MateMail` |
| `inbound.yourbrand.example` | `mx.matemail.online` |
| `spf.yourbrand.example` | `_spf.matemail.online` |
| `yb._domainkey` | `mm1._domainkey` |
| `admin.yourbrand.example` | `app.matemail.online` |
| `alerts@yourbrand.example` | `alerts@matemail.online` |
| `imap.yourbrand.example` | `imap.matemail.online` |
| `smtp.yourbrand.example` | `smtp.matemail.online` |

---

## UI Screens Inventory (from MateMailUI.jsx)

| Route | Component | Status |
|-------|-----------|--------|
| `landing` | LandingPage | ⬜ To build |
| `pricing` | PricingPage | ⬜ To build |
| `security` | SecurityPage | ⬜ To build |
| `docs` | DocsPage | ⬜ To build |
| `login` | Login | ⬜ To build |
| `signup` | Signup | ⬜ To build |
| `forgot` | Forgot | ⬜ To build |
| `verify` | VerifyEmail | ⬜ To build |
| `twofa` | TwoFactorSetup | ⬜ To build |
| `onboard-welcome` | OnboardWelcome | ⬜ To build |
| `onboard-domain` | OnboardDomain | ⬜ To build |
| `onboard-dns` | OnboardDNS | ⬜ To build |
| `onboard-verify` | OnboardVerify | ⬜ To build |
| `onboard-mailbox` | OnboardMailbox | ⬜ To build |
| `onboard-complete` | OnboardComplete | ⬜ To build |
| `dashboard` | AdminDashboard | ⬜ To build |
| `domains` | DomainsPage | ⬜ To build |
| `domains-detail` | DomainDetail | ⬜ To build |
| `mailboxes` | MailboxesPage | ⬜ To build |
| `aliases` | Aliases page | ⬜ To build |
| `forwarding` | Forwarding page | ⬜ To build |
| `dns-health` | DNS Health page | ⬜ To build |
| `spam` | Spam/Quarantine page | ⬜ To build |
| `queue` | Mail Queue page | ⬜ To build |
| `logs` | Logs page | ⬜ To build |
| `backups` | Backups page | ⬜ To build |
| `billing` | Billing page | ⬜ To build |
| `team` | Team Members page | ⬜ To build |
| `settings` | Settings page | ⬜ To build |
| `webmail` | WebmailInbox | ⬜ To build |
| `compose` | ComposeScreen | ⬜ To build |
| `webmail-thread` | ThreadView | ⬜ To build |
| `webmail-settings` | WebmailSettings | ⬜ To build |
| (platform admin) | Internal staff portal | ⬜ Phase 13 |
