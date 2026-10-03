# TODO.md — MateMail Task Tracker

**Product:** MateMail
**Last updated:** 2026-09-19
**Current phase:** Production Readiness P0 complete → next phase awaiting assignment

Legend: ✅ Done | 🔄 In Progress | ⬜ Pending | ❌ Blocked

> **This file's phase 1–17 checklists below are the ORIGINAL feature plan and are
> stale in two ways.** They still show Phase 1–2 tasks as pending although that
> work shipped, and they predate DEC-011 (MateMail as one integrated platform).
>
> The authoritative plan is now the **Production Readiness roadmap** in
> `PROJECT_STATUS.md`. The Phase 7 "Webmail Data Flow" checklist below was
> superseded by **P11 — PostBox**, which shipped the webmail product under
> `/api/postbox/` rather than the `/api/webmail/` shape sketched here. The
> checklist is kept for history; it is not the plan.
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
- ✅ Create webmail app — shipped as `postbox` (P11)

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

> **Superseded by P11 — PostBox.** Webmail shipped as `apps.postbox` under
> `/api/postbox/`, on its own hostname `postbox.matemail.online`, with a
> mailbox-bound session rather than a Workspace SSO bridge (DEC-049). The
> `/api/webmail/` endpoints below were never built and will not be: the URL
> tree is removed. Folders, messages, send, drafts, flags, folder actions,
> the inbox, reading pane, composer and folder navigation all exist in
> PostBox. Thread view is the one item genuinely still owed: PostBox lists
> and reads individual messages, and neither groups a conversation nor
> renders one as a thread.

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
| `admin.yourbrand.example` | `portal.matemail.online` |
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
| `webmail` | PostBox inbox | ✅ P11 (`/postbox`) |
| `compose` | PostBox composer | ✅ P11 (in-page, not a route) |
| `webmail-thread` | ThreadView | ⬜ To build |
| `webmail-settings` | PostBox settings | ✅ P11 (`/postbox/settings`) |
| (platform admin) | Internal staff portal | ⬜ Phase 13 |

---

## Found during P6 (2026-09-19)

⬜ **`backend/apps/backups/` reports backups that never happen.**
`/api/backups/` is live to tenant admins. `run_backup_task` counts domain and
mailbox rows, computes `size_mb` from `domain_count * 2 + mailbox_count * 5`,
records a `storage_location` that is never written to, and marks the job
COMPLETED. Nothing is read, copied or stored. The frontend at
`frontend/app/app/backups` presents this as a backup feature.

This is unrelated to the platform backups delivered in P6
(`docs/BACKUP_RESTORE.md`), which are real and have been restored.

Before Private Beta this must either be implemented on top of the P6 engine —
a per-tenant export is a plausible use of `restic dump` scoped to one domain's
mailboxes — or removed from the API and the UI. Shipping it as-is tells
customers their mail is backed up when it is not.

⬜ **No operator-facing purge for retired mailbox storage.** NE4 retains a
deleted mailbox's Maildir and records it in `retired_mailbox_storage`. P6 backs
that up and can restore from it, but nothing removes it: the rows and the
directories accumulate for the lifetime of the deployment. A retention policy
for retired storage belongs with P7 operations.

---

## Found during P7 (2026-09-19)

✅ **Docker had no log rotation at all.** No `/etc/docker/daemon.json` existed,
so the default `json-file` driver ran unbounded; container logs had reached
45 MB each with nothing to reclaim them. Fixed with
`/etc/logrotate.d/matemail-docker-containers`, which bounds them without the
Docker daemon restart that a `daemon.json` change would have required across
72 containers.

⬜ **`ALERT_RECEIVER_CONFIGURED = NO`.** Alerts fire, group and are visible in
Alertmanager over the SSH tunnel, but nothing is delivered off the host. Set
`ALERT_WEBHOOK_URL` in `/opt/MateMailMonitoring/.env` and re-run `install.sh`.
The destination must not be served by Native Postfix or MateMail's transactional sender. Pre-beta requirement; does not block NE6.

⬜ **`OFFSITE_BACKUP_CONFIGURED = NO`** (carried from P6, now monitored).
`BackupOffsiteNotConfigured` fires permanently and by design. Pre-beta
requirement; does not block NE6.

---

## Found during NE6 (2026-09-19)


⬜ **Stale comment in the Native compose networks block.** It describes the
engine network as "internal (no egress)". It is not, and must not be — Postfix
needs outbound SMTP, ClamAV needs signature updates, Unbound needs DNS. The
live network is `internal=false`, which is correct; only the comment is wrong.

---

## Found during NE7 (2026-09-20)

✅ **Inbound mail hard-bounced during a Dovecot restart.** `lmtp:inet:dovecot:24`
meant a stopped container produced NXDOMAIN, which Postfix treats as permanent
(`dsn=5.4.4`), returning customer mail to senders during ordinary maintenance.
Fixed by delivering to a pinned address (DEC-044).

✅ **fail2ban's IMAP filter matched nothing real.** It carried Dovecot 2.3's
wording and had been validated against a hand-written sample of the same
wording. Dovecot 2.4 says `Login aborted:`. Fixed, and the tests now use log
lines captured from the running server.

✅ **IMAP auth was invisible to monitoring for two independent reasons** — the
collector read only stdout while Dovecot logs to stderr, and the patterns used
2.3 wording. Both fixed.

✅ **Sender-login restrictions were declared on port 25 where Postfix ignores
them**, warning on every inbound connection. Moved to submission.


⬜ **No automatic client configuration** (autoconfig / autodiscover). Manual
settings are documented in `docs/MAIL_CLIENT_SETUP.md`. A product feature, not
a prerequisite.

⬜ **Microsoft and Zoho real inbox delivery untested.** Protocol interop to both
is verified (MX resolution, TCP/25, STARTTLS, TLS 1.3), but no
operator-controlled mailbox exists on either, and NE7 did not invent one.

---

## Found during PostBox remote push, server half (2026-09-26)

✅ **A plain `!include` of the optional push file would have taken Dovecot
down.** `dovecot.conf` ships with `git pull` and the entrypoint that renders
`engine-push.conf` ships in the image. Measured with the old image, the result
is `Fatal: ... No matches`: IMAP and LMTP stop over an optional feature. Fixed
with `!include_try`, and a test pins it.

✅ **Revoked sessions kept their push tokens.** A revoked session's
registrations were never selected, but they sat in the table until the session
was pruned, up to 37 days later. Revocation now deletes them.

⬜ **PostBox-App client integration** (the next task): FCM receiver and WNS
channel registration, the `installation_id`, and calls to
`/api/postbox/devices/`. The contract is `docs/POSTBOX_REMOTE_PUSH.md` §12.

⬜ **Provider accounts** (operator): a Firebase project and a service account
that may send; a Microsoft Entra ID app registration; the Windows App SDK
PFN → AppId mapping request to Microsoft. None of these exist.

⬜ **Deploy**: new Native API and Dovecot images, the three push secrets, and
the MateMail release with migration `postbox 0005`. Nothing is deployed.

⬜ **First live push**, to prove each adapter against the real provider. Until
then, FCM and WNS are verified only against documentation and mocked HTTP.

⬜ **Re-measure `folder` if Sieve is ever enabled** on the Native Engine. Today
every delivery lands in INBOX, and nothing claims more than the folder Dovecot
reports.

⬜ **The relay queue is in memory.** Events waiting in the API when it restarts
are lost; the push is missed and the mail is not. Revisit only if missed pushes
around deploys turn out to matter.


---

## PostBox UI follow-ups (2026-10-04)

✅ **Remove duplicate Sidebar storage/appearance footer** — Settings already exposes mailbox usage and appearance. Keep the three compact Light/System/Dark controls in the avatar's account card. Implemented in `fix/postbox-sidebar-cleanup-theme-profile`.

⬜ **Thread-view Original / full headers download parity** — Each expanded conversation message must expose its *own* Original (.eml) action using its exact folder + UID + UIDVALIDITY (no download of another member's headers). Preserve the existing Single Message action.

⬜ **Avatar profile card dismiss behavior** — Clicking outside should close the card; Escape should also close it. Clicking controls inside must not prematurely dismiss it. Preserve account switching and keyboard accessibility. This is a separate pending issue; not part of the Sidebar footer change.
