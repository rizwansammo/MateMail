# PHASES.md — MateMail Build Phases

**Product:** MateMail  
**Last updated:** 2026-05-29

Each phase must build and pass basic tests before the next phase begins. `PROJECT_STATUS.md` and `TODO.md` are updated at the end of every phase.

---

## PHASE 0 — Repo Audit and Project Plan ✅

**Goal:** Understand what exists, make architecture decisions, write all documentation.

**Tasks:**
- Inspect existing folders and files
- Identify frontend app, backend app, env files, Docker files, package files
- Find the UI prototype and extract required screens/routes/components
- Write docs/ARCHITECTURE.md
- Write docs/PHASES.md
- Write docs/TODO.md
- Write docs/DECISIONS.md
- Write docs/MAIL_ENGINE.md
- Write docs/SECURITY.md
- Write docs/DEPLOYMENT.md
- Write PROJECT_STATUS.md
- Recommend mail engine approach

**Acceptance:**
- Documentation exists
- Project can still install/build as before (N/A — repo was empty)
- No large implementation yet

**Status:** ✅ Complete

---

## PHASE 1 — Foundation, Docker, ENV, and Project Structure

**Goal:** Working Docker Compose environment with all services starting.

**Tasks:**
- Create Next.js frontend app (`frontend/`)
- Create Django backend app (`backend/`)
- Create Docker Compose with services:
  - `frontend` (Next.js)
  - `backend` (Django)
  - `postgres` (PostgreSQL 16)
  - `redis` (Redis 7)
  - `celery-worker`
  - `celery-beat`
  - `mailcow` (mail engine stack via mailcow's own docker-compose)
- Create `.env.example`
- Never commit real secrets
- Add required environment variables
- Add healthcheck endpoints:
  - `GET /api/health/`
  - `GET /api/health/db/`
  - `GET /api/health/redis/`
  - `GET /api/health/mail-engine/`
- Configure Nginx or Traefik as reverse proxy stub

**Acceptance:**
- `docker compose up` starts base services
- Backend health endpoint returns 200
- Frontend starts and renders
- PostgreSQL and Redis are reachable from backend
- `PROJECT_STATUS.md` updated

---

## PHASE 2 — Django Core Models and Multi-Tenancy

**Goal:** All core data models defined with tenant isolation enforced.

**Django apps to create:**
`accounts`, `tenants`, `domains`, `mailboxes`, `dnshealth`, `aliases`, `forwarding`, `mailqueue`, `logs`, `quarantine`, `backups`, `billing`, `teams`, `platform_admin`, `webmail`

**Key models:**
- `Tenant`, `User`, `TenantMembership`
- `Domain`, `Mailbox`, `Alias`, `ForwardingRule`
- `DNSRecordCheck`, `MailLog`, `QueueMessage`
- `QuarantineMessage`, `BackupJob`
- `Plan`, `Subscription`, `Invoice`

**Acceptance:**
- Migrations pass
- Django admin can view core models
- Tests verify tenant isolation for domains, mailboxes, aliases, logs

---

## PHASE 3 — Auth, Signup, Email Verification, 2FA, Roles

**Goal:** Complete authentication flow from signup to 2FA.

**APIs:**
- POST `/api/auth/signup/`
- POST `/api/auth/login/`
- POST `/api/auth/logout/`
- POST `/api/auth/forgot-password/`
- POST `/api/auth/reset-password/`
- POST `/api/auth/verify-email/`
- POST `/api/auth/2fa/setup/`
- POST `/api/auth/2fa/verify/`
- GET `/api/me/`
- GET `/api/workspaces/`

**Frontend screens:**
- Login, Signup, Forgot password, Email verification, 2FA setup

**Acceptance:**
- User can create account
- User verifies email
- User sets up 2FA
- User reaches onboarding
- Role permissions work

---

## PHASE 4 — Workspace Onboarding Flow

**Goal:** New user can add domain, see DNS records, create first mailbox.

**APIs:**
- POST `/api/onboarding/workspace/`
- POST `/api/domains/`
- GET `/api/domains/:id/dns-records/`
- POST `/api/domains/:id/verify/`
- POST `/api/mailboxes/`
- GET `/api/onboarding/status/`

**Frontend:** 6-step onboarding stepper from prototype

**Acceptance:**
- New user can create workspace
- DNS records are generated and shown as copyable cards
- User can create first mailbox
- Onboarding complete routes to dashboard

---

## PHASE 5 — DNS Verification and DKIM Generation

**Goal:** Real DNS verification using live DNS resolvers.

**Tasks:**
- Generate DKIM via mailcow API per domain
- Retrieve DKIM public key and store for display
- Verify MX, SPF, DKIM, DMARC, MTA-STS, TLS-RPT
- Calculate DNS health score
- Celery scheduled task for periodic re-checks
- Manual retry button

**Acceptance:**
- DNS verification works against real domains
- DNS health page shows current status
- Retry verification updates status
- Tests cover passing and missing DNS records

---

## PHASE 6 — Real Mail Engine Integration: Domains, Users, Auth

**Goal:** MateMail provisions domains/mailboxes into mailcow so IMAP and SMTP work.

**Django service layer methods:**
- `provision_domain(domain)`, `suspend_domain(domain)`
- `provision_mailbox(mailbox)`, `disable_mailbox(mailbox)`
- `update_mailbox_password(mailbox)`, `update_mailbox_quota(mailbox)`
- `provision_alias(alias)`, `remove_alias(alias)`
- `provision_forwarding(rule)`, `remove_forwarding(rule)`
- `sync_mail_engine_state()`

**Acceptance:**
- Create domain → exists in mailcow
- Create mailbox → IMAP login works
- SMTP submission works with mailbox credentials
- Disabled mailbox cannot login
- Suspended tenant cannot send
- No open relay test passes

---

## PHASE 7 — SMTP Send, Receive, IMAP Read, and Webmail Data Flow

**Goal:** End-to-end email working through MateMail webmail.

**Backend webmail APIs:**
- GET `/api/webmail/folders/`
- GET `/api/webmail/messages/?folder=inbox`
- GET `/api/webmail/messages/:id/`
- POST `/api/webmail/send/`
- POST `/api/webmail/drafts/`
- PATCH `/api/webmail/messages/:id/`
- POST `/api/webmail/messages/:id/reply/`
- POST `/api/webmail/messages/:id/forward/`
- POST `/api/webmail/messages/:id/archive/`
- POST `/api/webmail/messages/:id/trash/`
- POST `/api/webmail/messages/:id/mark-read/`

**Frontend:** Full webmail UI (inbox, reading pane, compose, thread, folders)

**Acceptance:**
- Real received mail appears in Inbox
- Compose sends real email
- IMAP client and webmail show consistent state

---

## PHASE 8 — Admin Dashboard and Customer Control Center

**Goal:** All admin dashboard pages use real API data (no mock data).

**APIs:**
- GET `/api/dashboard/overview/`
- All resource CRUD APIs (domains, mailboxes, aliases, forwarding, etc.)

**Acceptance:**
- Dashboard cards show real data
- Every page uses real API data
- Empty/loading/error states work
- Tenant isolation tests pass

---

## PHASE 9 — Aliases and Forwarding

**Goal:** Aliases and forwarding rules work end-to-end.

**Acceptance:**
- Mail to alias delivers to destination
- Forwarding sends to external destination
- keep_copy works
- Disabled rule stops routing

---

## PHASE 10 — Spam, Quarantine, Queue, and Logs

**Goal:** Operational visibility pages populated with real data.

**Acceptance:**
- Quarantine page shows held messages
- Queue page shows real queue
- Logs page searchable/filterable
- Sensitive data not leaked in logs

---

## PHASE 11 — Billing, Plans, Limits, and Tenant Suspension

**Goal:** Plan limits enforced, billing page functional.

**Plans:** Starter ($9), Business ($29), Infrastructure ($99)

**Acceptance:**
- Plan limits enforced at API level
- Suspended tenant cannot send outbound
- Invoice history shown

---

## PHASE 12 — Team, RBAC, Security Settings, API Keys

**Goal:** Full team management and role enforcement.

**Roles:** Owner, Admin, Support, Read-only

**Acceptance:**
- Read-only cannot mutate
- Owner manages billing and team
- API keys can be created and revoked

---

## PHASE 13 — Internal Platform Admin

**Goal:** Internal MateMail staff portal.

**Navigation:** Tenants, Plans, Usage, Invoices, Support, System Health, Abuse Reports, Audit Logs, Settings

**Acceptance:**
- Only platform admins can access
- Platform admin actions are audit logged

---

## PHASE 14 — Backups and Restore Visibility

**Goal:** Backup job status visible, restore action safe.

**Acceptance:**
- Backup page shows real/documented backup status
- Failed backups show warning
- Restore action is role-protected

---

## PHASE 15 — Deployment, TLS, DNS, and Production Hardening

**Goal:** Production deployment on Linux VPS.

**Domains:**
- `matemail.online`, `app.matemail.online`, `webmail.matemail.online`
- `docs.matemail.online`, `imap.matemail.online`, `smtp.matemail.online`, `mx.matemail.online`

**Acceptance:**
- HTTPS works
- SMTP/IMAP TLS works
- Mail clients can connect
- No open relay
- Deployment docs complete

---

## PHASE 16 — Testing and Quality

**Goal:** Comprehensive test coverage.

**Tests:**
- Tenant isolation, auth, 2FA, domain creation, DNS verification
- Mail engine provisioning adapter
- Disabled mailbox auth behavior
- Suspended tenant send block
- Alias routing, forwarding
- RBAC, billing limits, platform admin access

**Acceptance:**
- Unit tests pass
- Backend lint passes
- Frontend build passes
- Manual mail test checklist passes

---

## PHASE 17 — Final Polish

**Goal:** Production-ready UI and infrastructure.

**Tasks:**
- Replace all placeholder brand text with MateMail
- Fix all hostnames (`inbound.yourbrand.example` → `mx.matemail.online`, etc.)
- UI must never show backend engine names
- Clean landing page
- Professional Gmail-like webmail
- Mobile responsiveness
- Toast notifications
- Copy-to-clipboard feedback
- Final documentation

**Acceptance:**
- MateMail branding consistent
- All major flows connected and working
- IMAP/SMTP working
- Deployment docs complete
