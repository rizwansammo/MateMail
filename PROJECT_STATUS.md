# PROJECT_STATUS.md

**Product:** MateMail  
**Owner:** NetaMate Solutions  
**Domain:** matemail.online  
**Last updated:** 2026-09-12  
**Current phase:** **P5 COMPLETE** (2026-09-13) — mail policy activated in production  
&nbsp;&nbsp;&nbsp;&nbsp;Release `307c81c` deployed. Policy bridge live at 10.244.0.246:10031, private only.  
&nbsp;&nbsp;&nbsp;&nbsp;Postfix consults MateMail before `permit_sasl_authenticated`, and once per  
&nbsp;&nbsp;&nbsp;&nbsp;message at end-of-data. Relay, spoof, approval, suspension, rate-limit and  
&nbsp;&nbsp;&nbsp;&nbsp;fail-closed behaviour verified against the live engine.  
&nbsp;&nbsp;&nbsp;&nbsp;**No public mail port is open. No customer mail is enabled. Private Beta has not started.**  
**Previous phase:** **P4 COMPLETE** (2026-09-12) — Mail Engine live, activated, and delivering  
&nbsp;&nbsp;&nbsp;&nbsp;P4A design + adapter · P4B private engine install · P4C-A/A2/A3 remediation  
&nbsp;&nbsp;&nbsp;&nbsp;· P4C-B activation, released as `b8e0fe3b`.  
&nbsp;&nbsp;&nbsp;&nbsp;Production runs `MAIL_ENGINE_ADAPTER=native` against the MateMail Native Engine.  
&nbsp;&nbsp;&nbsp;&nbsp;**Transactional delivery validation: PASSED** (2026-09-12) — one real message  
&nbsp;&nbsp;&nbsp;&nbsp;delivered to Gmail Primary Inbox with SPF, DKIM and DMARC all passing.  
&nbsp;&nbsp;&nbsp;&nbsp;No public mail port is open. No customer domain or mailbox exists.  
&nbsp;&nbsp;&nbsp;&nbsp;**Not ready for customer mail:** P6, P7 and P7.5 remain before Private Beta.  
**Next phase:** P6 — backups, restore, retention, safe deletion  
**Launch gate:** private beta requires all of P0–P7; public launch requires P9

---

## Mail collaboration foundation — feature branch (2026-10-06)

Development branch: `feature/mail-collaboration`.

Phase A establishes the shared-address and mailbox-access primitives for
TeamBox, Forward Group and Delegation. Phase B normalizes Alias as an alternate
address for exactly one existing MateMail mailbox. Phase C adds full TeamBox
Hub/backend/native-engine management, including passwordless storage and
engine-enforced member sender authorization. Phase D adds permission-aware
TeamBox switching and use inside PostBox while keeping the authenticated
personal mailbox identity immutable.

- Central `AddressClaim` registry prevents cross-type address collisions.
- Existing personal mailboxes and aliases are backfilled into the registry.
- `Mailbox.kind` distinguishes personal mailboxes from future TeamBoxes.
- Direct PostBox authentication remains personal-mailbox only.
- `MailboxAccessGrant` is the permission primitive for TeamBox membership and
  Delegation.
- Existing `ForwardingRule` remains separate from Forward Group.
- Phase B removes external Alias destinations from the product contract; external delivery remains the existing Forwarding feature.
- Phase C adds dedicated TeamBox Hub screens, member permissions, native-engine schema v5 and SMTP sender authorization.
- Phase D adds PostBox TeamBox switching, Read/Manage/Send As/Send on behalf enforcement, personal-actor SMTP submission, shared Sent/Drafts, scheduled-send actor retention and audit logging.

Design record: `docs/COLLABORATION.md`.

## Custom Hub/PostBox Domains — production live (2026-10-06)

The custom-host subsystem is deployed and production-accepted.

- ✅ Backend hostname ownership, DNS verification and dynamic Host guard.
- ✅ Root-owned nginx/Certbot provisioning and automatic certificate lifecycle.
- ✅ Surface-aware Hub/PostBox routing with cross-surface fail-closed behavior.
- ✅ Hub self-service custom access URL UI and automatic HTTPS activation.
- ✅ NetaMate production acceptance:
  `mailhub.netamate.com` and `postbox.netamate.com` are ACTIVE custom hosts.
- ✅ Legacy NetaMate dedicated layer retired: no port 3060 frontend, no
  `DEDICATED_TENANT_HOSTS`, no fixed NetaMate host entries, no
  `/opt/NetaMate-Email` deployment.

Phase 6 (canonical Hub hostname migration from `portal.matemail.online` to
`hub.matemail.online`) remains a separate future change and is not required
for customer custom URLs.

Full design and acceptance record: `docs/CUSTOM_DOMAINS.md` and
`docs/CUSTOM_DOMAIN_NETAMATE_PILOT.md`.

## Architecture direction

As of **DEC-011 (2026-09-10)**, MateMail is one integrated business email
platform. Native Postfix / Dovecot / Rspamd are MateMail's
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
| 4 | ~~No webmail.~~ **PostBox is built (P11)** — inbox, reader, composer, folders, search, contacts, signatures, rules and settings, against real IMAP. TBD-G is resolved: a Dovecot master identity, with the person's own password used once at sign-in and never stored (DEC-051). The SSO bridge is removed rather than finished — it could mint a login token for any mailbox in a tenant (DEC-049). **Not yet proven in production:** the code is complete and tested locally, but no mail has been read or sent through PostBox on MateServer, because the Dovecot image carrying the master identity has not been built and deployed. | Built, unproven in production |
| 5 | **Queue, quarantine and storage usage are empty shells.** Nothing writes `QueueMessage` or `QuarantineMessage`; `storage_used_mb` is never assigned. No mailcow→MateMail sync task exists. | Open |
| 6 | ~~Domain ownership is not verified before a domain is provisioned into the mail engine.~~ | ✅ Closed in P3a |
| 7 | ~~Deployment architecture is contradictory.~~ | ✅ Closed in P2 |
| 8 | ~~Engine detail can reach customers.~~ | ✅ Closed in P1 |
| 9 | ~~Production CSP required `script-src 'unsafe-inline'`.~~ | ✅ Closed in P3c |
| 10 | **Transactional email is unproven.** The code path is built and tested, but no message has ever actually been delivered. Account verification and password reset depend on it, so a customer can neither complete signup nor recover an account until it works. Per **DEC-013** this is now delivered by MateMail's own Mail Engine rather than a third-party provider, so proving it is **P4 work**. | Open — deferred to P4 |

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
| P1 | Harden the Mail Engine boundary | ✅ Complete (2026-09-11) |
| P2 | Deployment alignment (NetaMate model, GHCR, ports 8020/3020) | ✅ Complete (2026-09-11) |
| P2.5 | Controlled production deployment of the control plane | ✅ Complete (2026-09-10) — Mail Engine NOT deployed |
| P3 | Domain ownership verification + abuse prevention | ✅ Complete (2026-09-11) — implementation + CI; delivery validation deferred |
| P4A | Mail Engine architecture, adapter boundary | ✅ Complete (2026-09-11) — committed `9594376` |
| P4B | Private Mail Engine installation and validation | ✅ Complete (2026-09-11) — engine live and private, **not activated** |
| P4C-A | Engine contract remediation | ✅ Implementation complete (2026-09-11) |
| P4C-A2 | Private engine boundary + durable deprovisioning | ✅ Implementation complete (2026-09-11) |
| P4C-A3 | Versioned engine infrastructure + exact-revision Compose deployment | ✅ Implementation complete (2026-09-11) |
| P4C-B | Activation: real adapter, delivery validation | ✅ Complete (2026-09-12) — real mail delivered, SPF/DKIM/DMARC pass |
| **P4** | **Mail Engine: customer email + platform transactional mail** | **✅ COMPLETE (2026-09-12)** |
| P5 | Mail policy, approval, abuse controls, product enforcement | ✅ **COMPLETE (2026-09-13)** — activated in production at `307c81c`; policy bridge live, Postfix hook enforcing |
| P6 | Backups, restore, safe deletion | Pending |
| P7 | Operational surface (sync, reconciliation, monitoring, alerting) | Pending |
| P7.5 | **MateMail Free** — `username@matemail.online` accounts (DEC-015) | Pending |
| — | **PRIVATE BETA gate** — requires all of P0–P7.5; tests **both** business and free mailboxes | Blocked on P7.5 |
| P9 | Public launch readiness | Pending |
| — | **PUBLIC LAUNCH** | Blocked on P9 |
| P8 | MateMail webmail — **delivered as P11 (PostBox)**; built and tested, not yet deployed | Superseded by P11 |

Full detail, entry criteria and exit criteria: *Revised roadmap* below.

### Native Engine Migration (NE0–NE8) — COMPLETE

A **separate** engineering track that replaces mailcow as the Mail Engine
orchestrator with a MateMail-native stack on Postfix, Dovecot and Rspamd. It does
**not** renumber or replace any P phase.

```
NE0 — architecture design:     COMPLETE (2026-09-13) — see DEC-019
NE1 foundation:                COMPLETE (2026-09-13) — 10/10 services deployed and
                               healthy on MateServer, isolated, restart-recovered,
                               digest-pinned (see deploy/native-engine/)
NE2 provisioning:              COMPLETE (2026-09-18) — schema, Native API, DKIM
                               lifecycle, native adapter, tests, MateServer
                               runtime validation and the deployment-integrity
                               fix; the API runs entirely from its pinned image
NE3 mail flow:                 COMPLETE (2026-09-18) — validated on MateServer.
                               Postfix, Dovecot and Rspamd consume NE2 state:
                               authenticated submission -> sender authorisation
                               -> Rspamd (ClamAV, oletools, DKIM signing) -> LMTP
                               -> Maildir. DKIM verified cryptographically, not
                               by header inspection.
NE4 operations:                COMPLETE (2026-09-19) — validated on MateServer.
                               Immutable mailbox storage, real usage from
                               Dovecot, enforced per-mailbox rate limits, queue
                               and quarantine control. Adapter 26 of 26.
NE5 control-plane switch:      COMPLETE (2026-09-19) — MAIL_ENGINE_ADAPTER=native
                               in production. MateMail provisions through the
                               Native Engine; mailcow stays installed as the
                               rollback path and the live mail transport.
P6 backup and restore:         COMPLETE (2026-09-19) — restic, encrypted,
                               daily timer. Full restore drill and
                               single-mailbox restore both PROVEN on
                               MateServer. NO OFFSITE REPOSITORY yet, so
                               this is retention, not disaster recovery.
P7 monitoring:                 COMPLETE (2026-09-19) — deployed on MateServer,
                               alerts validated against real induced failures.
                               Prometheus/Grafana/Alertmanager loopback-only.
                               NE6 technical readiness reports READY.
NE6 platform outbound:         COMPLETE (2026-09-19) - MateMail platform
                               transactional mail now leaves through the
                               Native Engine. SPF/DKIM/DMARC all pass,
                               Gmail Primary Inbox. Mailcow retained as
                               rollback only; no automatic fallback.
NE7 public mail:               COMPLETE (2026-09-20) - 25/587/993 public.
                               Real Gmail inbound delivered and read over
                               public IMAPS; external client submission
                               reached Gmail Inbox with SPF/DKIM/DMARC pass.
                               110/143/465/995 closed. Mailcow untouched.
NE8 implementation:            COMPLETE - 2026-09-29
mailcow:                       RETIRED - no containers, volumes, networks, images or runtime dependency
```

**NE5 control-plane switch on MateServer (2026-09-19)**

```
release        37ebb2176f74bb9a16a3cc56e678326da379cae3
CI             run 35437334621 (success)
deploy         run 35437913166 (success)
images         no Native Engine image changed; MateMail backend rebuilt
adapter        MAIL_ENGINE_ADAPTER=native on backend, worker and beat
network        Native `api` joined matemail_engine_link; MateMail reaches the
               API and CANNOT reach postfix, dovecot or the engine database
workflows      24/24 through NativeMailEngineAdapter from the application
celery         mail_engine.deprovision_mailbox executed against Native
outage         API stopped -> EngineUnavailable, adapter stayed Native, no
               mailcow mutation; recovered cleanly
mailcow        0 synthetic objects created; 20 containers untouched
transactional  EMAIL_* unchanged (mx.matemail.online:587, noreply@)
isolation      UFW identical, DNS/PTR unchanged, 0 native public ports,
               0 non-loopback mail ports, 0 outbound SMTP from Native
```

One defect was found and fixed during the switch: `NATIVE_ENGINE_API_URL` and
`NATIVE_ENGINE_API_SECRET` were set in `.env` but not declared in
`deploy/docker-compose.yml`, so Compose passed them through as empty. The NE5
deployment checks caught it (`mail_engine.E011/E012`) before any customer action.

**NE4 runtime validation on MateServer (2026-09-19)**

```
release        8f9fb952250903fe5f46a4c2153ee2ef5e409a6e
CI run         35399071707 (success)
image runs     35399633726 (api), 35399730060 (dovecot), 35399812048 (postfix)
api digest     sha256:0a13dae9b251b08087f325d0ae41e1da0660b789e201f822ab8423da9b6c52d1
dovecot digest sha256:e4f545002fa6889baa1530a655cb31c13eeec7f3dbc48f4a75e38c6b9f2efeae
postfix digest sha256:7e20c9c53f070d4ddbd821cb6e5b47f3980acb62042249524864fc928bd240b0
               (superseding 817dbb22 — the rate-limiter fail-safe fix,
                release 7cb10d1d, CI 35408935481, image run 35409244833)
schema         v3 -> v4 (migration 004 applied exactly once)
services       10/10 healthy, 0 published ports, 0 API bind mounts
storage        delete + recreate yields a new opaque identity; the recreated
               mailbox reported 0 messages while the old Maildir remained
usage          0 MB/0 msgs empty, 2 MB/1 msg after a 2.5 MB delivery
rate limit     2/minute: 2 accepted, 3rd deferred 450 4.7.1; clear restores
               counter outage: a mailbox WITH a limit defers 4.7.1, a mailbox
               without one is unaffected, enforcement resumes on its own
queue          exact-id cancel removed the message; never delivered; idempotent
quarantine     held, listed, released -> delivered exactly once, recipient intact
failure modes  ClamAV down 451, Rspamd down 454, Dovecot down 454,
               PostgreSQL down 451 (no relay, no spoof bypass), Unbound down
               451, Redis down 451 for limited mailboxes; all recovered
               EICAR rejected 554 with CLAM_VIRUS(2000.00)
adapter        26/26 exercised through NativeMailEngineAdapter against the server
rspamd deploy  deploy.sh config hash recreated Rspamd automatically (NE3 fix)
production     mailcow 20 + MateMail 8 untouched, UFW identical, DNS/PTR
               unchanged, 0 outbound SMTP, MAIL_ENGINE_ADAPTER still "mailcow"
```

**NE3 runtime validation on MateServer (2026-09-18)**

```
release        64b19471c39e51c0cfc8efe35a0b777ac3c2ba51
CI run         35382656549 (success)
image run      35384065561 + 35384352926 (Native Engine images)
api digest     sha256:abfa87acb7751ec6daf03842b1a9bedd120e8eb276673d725b5a8b195fb8be14
dovecot digest sha256:d90ce8e51e5c6c169736c0d95ecc25dcaccac7d20e0bb1d8bded9b3f903f94c5
postfix digest sha256:1f00551f026be80ce61d770f3c061db3a3a035901bc71259ac04fa649742e1eb
schema         v3 (migration 003 applied exactly once)
services       10/10 healthy, 0 published ports, 0 API bind mounts
end-to-end     PASS — alice -> bob, ESMTPSA -> Rspamd -> LMTP -> Maildir 5000:5000
DKIM           PASS — body hash + header signature verify against the API's
               public material; unrelated key correctly FAILS
rotation       PASS — old key retained 0600 11333:11333 with .retired marker,
               new selector authoritative, new message verifies
security       18/18 reader isolation, 12/12 spoof/relay matrix, EICAR rejected
               554, quota enforced, last_login stamped only on success
production     mailcow 20 + MateMail 8 untouched (StartedAt predates session),
               UFW checksum identical, DNS/PTR/MX unchanged, 0 outbound SMTP
```

**Adapter methods: 18 of 26 implemented, 8 capability-missing, all attributed to
NE4.** None is on the NE3 mail path.

One behaviour recorded for a later phase: deleting a mailbox removes its
provisioning rows but not its Maildir. That is deliberate for now — destroying
customer mail should be an explicit operation, not a side effect — but the
adapter contract does not yet say who owns that deletion. NE4 should decide.

**Product decision (2026-09-13): the Private Beta runs on the Native Engine, not
on mailcow.** Sequencing is therefore NE1–NE5 → P6 → P7 → NE6 → NE7 → P7.5 →
Private Beta → NE8. P6 waits so backup tooling is written once against the store
customers will actually use; NE6 waits because moving the platform sender moves
the sending reputation.

The port boundary means this is an adapter swap, not a rewrite: every mailcow
reference in `backend/` outside `mailcow_adapter.py`, `factory.py` and
`checks.py` is a comment.

Plan, acceptance gates, rollback strategy and the P6–P9 interaction analysis:
**`docs/NATIVE_MAIL_ENGINE.md`**. mailcow may be removed only at NE8, under its
own explicit authorization.

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
| L1 | **RESOLVED in P1.** `apps/mailboxes/views.py:73` writes `str(exc)[:500]` straight into `mail_engine_error`, which `MailboxSerializer` exposes. A connection failure puts the internal engine hostname and port in the customer UI. **Unsanitized.** | High |
| L2 | **RESOLVED in P1.** `_sanitize()` is case-sensitive and term-limited. Verified: `Mailcow`, `MAILCOW`, `postfix`, `dovecot`, `SOGo`, `Roundcube`, `ClamAV` and engine API paths all pass through unchanged — 7 of 11 realistic probes leaked. Even a matched case leaves structure like `HTTPConnectionPool(host='…-nginx', port=8080)`. | High |
| L3 | **RESOLVED in P1.** `ProvisionResult.raw` carries the engine's response dict through the port. Not currently serialized to customers, but nothing prevents it. | Medium |
| L4 | **RESOLVED in P1.** `provision_forwarding` / `delete_forwarding` query `apps.forwarding.models` and implement `keep_copy` and active-rule-set semantics **inside the adapter**. This is MateMail product logic below the port: a replacement engine would have to reimplement it. | Medium |
| L5 | **RESOLVED in P1.** Adapter methods take Django model instances, coupling the port to the ORM. `tasks.py` already works around this with `_FakeDomain` / `_FakeMailbox` shims. | Medium |
| L6 | **RESOLVED in P11.** `WEBMAIL_BASE_URL` was never read by any code, so it could only ever have done harm. The setting is deleted from `base.py`, the compose file and the env example. Webmail is PostBox, served from `postbox.matemail.online` by MateMail's own frontend image and routed by Host — there is no configurable address for it to be aimed at. | Medium |
| L7 | **RESOLVED in P1.** `/api/health/` is public and reports `mail_engine` status, disclosing that a distinct mail engine exists and whether it is up. | Low |
| L8 | **RESOLVED in P1.** Docstrings in `smtp_policy/views.py` and `webmail/views.py` name Postfix, Dovecot, Roundcube and SOGo. Internal-only, but should be reframed as Mail Engine components. | Low |
| L9 | **OPEN — deferred to P4 per DEC-007r.** The port is now correct (`rotate_dkim_key` returns public material only, and no DTO may carry a private key), but the legacy column still exists. DKIM private key held in the control plane: Django generates the keypair, stores the PEM unencrypted in `Domain.dkim_private_key`, and pushes it to the engine over plaintext HTTP. Violates DEC-007r: the signing key must be generated and stored inside the engine, with MateMail reading only the public half. | High |

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

### Launch sequence

```
P0 ✅  critical security + baseline
P1 ✅  Mail Engine boundary
P2     deployment alignment          ─┐
P3     ownership + abuse prevention   │ no engine, no VPS
P4     stand up the Mail Engine      ─┤
P5     mail policy enforcement        │ engine live, no customers
       + free-account policy DESIGN   │
P6     backups + safe deletion        │
P7     operational surface           ─┘
P7.5   MateMail Free — implement username@matemail.online
────────────────────────────────────────────────────────────────
PRIVATE BETA   ~5–10 friendly tenants, real domains, real mail
               PLUS a capped number of free @matemail.online accounts
────────────────────────────────────────────────────────────────
P9     public launch readiness
────────────────────────────────────────────────────────────────
PUBLIC LAUNCH

P8     MateMail webmail — before or after P9, product's call.
       NOT a launch blocker.
```

**P0–P7 must all be complete before any real customer mail reaches the
platform, and P7.5 before any free @matemail.online account exists.**
sync and operational alerting arrive. Without them an operator cannot see what
the platform is doing, cannot detect drift between MateMail and the engine, and
would not be told when mail stops flowing or when outbound volume spikes. Real
customer mail must not go on a platform in that state.

**P8 is explicitly not a launch blocker.** Customers use Outlook, Apple Mail,
Thunderbird, phone mail apps and any other standard IMAP/SMTP client, served by
the Email Clients page shipped in P4. Webmail may land before or after P9 as
product priority dictates. Changing that requires an explicit decision recorded
in `docs/DECISIONS.md`.

**P9 gates the broader public and commercial launch**, not the private beta.

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
- Replace the interim `script-src 'unsafe-inline'` CSP with a per-request
  nonce issued by Next.js middleware, and stop nginx setting CSP for the
  frontend. Required because the App Router emits inline hydration scripts;
  see the P2.5 CSP incident. Pair this with the httpOnly cookie move — the
  two together are what make an XSS survivable.
- Route transactional mail on a dedicated subdomain. *(Superseded by DEC-013:
  the subdomain stands, but delivery is MateMail's own Mail Engine rather than
  an external provider, and the delivery test moves to P4.)*
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

**Application side complete (2026-09-12).** See DEC-017, DEC-018 and
`docs/MAIL_POLICY.md`. Blocker 2 is **partially** closed: MateMail now makes the
right decisions, but the Mail Engine does not yet consult them at submission.

Landed:

- ✅ Approval gate — signup creates `PENDING_APPROVAL`; mail capability requires
  a mail-enabled status **and** an `approved_at` granted by a named admin. One
  authoritative check (`apps/tenants/policy.py`) that every provisioning path
  calls, including the Celery task that previously checked nothing.
- ✅ Rate limiter is genuinely atomic (one Redis script), counts per message,
  and reads its limits from the plan. Fails closed on a Redis outage.
- ✅ Tenant suspension deactivates the workspace's domains in the engine, so
  suspension is two independent mechanisms rather than one database column.
  Whether the engine-side half was queued is reported back to the operator.
- ✅ Graded abuse responses: suspend one mailbox, disable one workspace's
  outbound, or suspend the workspace. All reversible, all audited with an actor.
- ✅ Inbound policy understands aliases — previously every alias address was
  rejected as nonexistent, which would have permanently bounced all alias mail.
- ✅ Refusals classified permanent vs temporary, so a reversible condition
  defers instead of destroying the sender's message.
- ✅ Platform sender recognised explicitly — it has no mailbox, domain or
  tenant, and would otherwise have been rejected the moment the policy service
  was enforced, taking account recovery down with it.
- ✅ Periodic ownership re-verification, flag-only.
- ✅ Message-ID rooted at the sending domain instead of the container id.
- ✅ Free-account policy framework designed (DEC-018), nothing implemented.

**Engine-side integration — built in the repository, not yet deployed:**

- ✅ Postfix restriction ordering fixed. The policy hook sits *after*
  `permit_mynetworks` and *before* `permit_sasl_authenticated`, so authenticated
  submission reaches it instead of short-circuiting — the whole of blocker 2.
  Both placements are wrong in different ways, and the file says which and why.
- ✅ A second hook at `smtpd_end_of_data_restrictions`, which upstream leaves
  empty, so a message is counted once rather than once per recipient. The stage
  is passed to MateMail explicitly (`stage=rcpt` authorizes without counting).
- ✅ Policy bridge reachable over the existing private link, as a sidecar in the
  engine's own Compose project — no host port, no host networking, no other
  Docker network with access. The earlier host-daemon design would have rebuilt
  the published-socket topology DEC-014 measured and rejected; its systemd unit
  has been removed rather than left beside the replacement.
- ✅ `scripts/install-policy-bridge.sh` written, with a `--check` mode, a
  preflight that refuses to install without the shared secret, and a
  verification step that prints the engine's own effective `postconf` output.
- ✅ Sender authorization extended to aliases, matching the engine's own sender
  ACL, so the two agree instead of one refusing what the other permits.

**Remaining: deployment.** Postfix is not running in CI, so what cannot be
proved here is that the engine invokes the hook at the configured stages. The
installer ends by printing the effective configuration for exactly that reason.
See `docs/SECURITY.md` § No-Open-Relay Checklist, item D.

**Deliberately deferred, with reason:**

- ⬜ Destination confirmation before a forwarding rule activates, with notice to
  the mailbox owner and workspace owner. This is a product feature — a
  confirmation token, an email flow, and UI — rather than a policy fix, and
  building it inside a policy phase would have meant shipping it without the
  interface design it needs. It remains a **Private Beta blocker**: external
  forwarding is the highest-abuse-risk capability MateMail offers, and it must
  not reach real customers unconfirmed.

Also in P5, **design only**: the policy framework free `@matemail.online`
accounts will need at P7.5 (DEC-015). Free public email carries a categorically
higher abuse risk than business hosting — a business mailbox costs a domain
someone paid for, a free one costs a signup form — and these controls cannot be
retrofitted under load onto reputation shared with paying customers.

Topics to cover: free mailbox storage quota; daily and hourly sending limits;
recipient limits per message and per period; anti-spam thresholds; signup abuse
controls and bot protection; account suspension; inactive-account policy;
reserved usernames; username lifecycle and re-use; recovery and verification
rules; abuse reporting; rate limits.

**No numeric limit is fixed in advance.** Choosing a daily send cap before P5
has analysed real behaviour would be inventing a number and then defending it.

**Exit:** every box in the `SECURITY.md` no-open-relay checklist is ticked by an
automated test that speaks real SMTP, and the free-account policy framework is
designed and written down — not implemented.

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

### Private Beta gate — after P7

Not a build phase: the checkpoint where real customer mail is first allowed onto
the platform. Entry requires **every one of P0–P7 complete**, not merely started.

Entry criteria:

- Mail flows end to end: a mailbox created in the MateMail UI sends to and
  receives from external providers (P4).
- No open relay, sender-equals-authenticated enforced, suspension stops
  outbound, rate limits apply — each proven by an automated test against real
  SMTP (P5).
- A tenant's mail has been **restored** from an offsite backup into a clean
  environment, timed and documented. Soft delete with a retention hold is live
  (P6).
- Queue, quarantine and per-mailbox storage reflect real engine state; drift
  reconciliation runs; alerting exists for outbound spikes, blocklist
  appearances, disk pressure on the mail volume, and app/mail port downtime (P7).
- An operator can run a normal week without opening the engine's own admin UI (P7).
- Customers have a supported mail path: the Email Clients page with IMAP/SMTP
  settings and client setup guides (P4).

Beta shape: **around 5–10 friendly tenants** on real domains, **plus a capped
number of free `@matemail.online` accounts** so both product paths are exercised
(DEC-015). Watch for at least two weeks before considering wider access. Treat
every incident as a P9 input.

Exit: no unresolved incident affecting mail delivery, and alerting has fired at
least once on a real condition or a deliberate drill.

---

### P7.5 — MateMail Free accounts

**Roadmap only. Nothing is implemented, and nothing about this belongs in P4.**
Full rationale in DEC-015.

Implements free `username@matemail.online` mailboxes on a domain MateMail owns —
conceptually what `@gmail.com` is to Gmail — alongside, not instead of, the
business custom-domain product.

Expected scope: public username availability; reservation and normalisation
rules; reserved and system usernames; mailbox creation on `matemail.online`;
free plan assignment and quota enforcement; sending policy integration; signup
verification; account recovery; suspension and deactivation; inactive-account
handling; admin controls; abuse controls; mailbox lifecycle; and how a free
account maps onto MateMail's tenant/account ownership model.

**A reserved-address system must ship before any username can be claimed.**
`postmaster` and `abuse` are required by RFC 2142 and are how other operators
report problems; `admin`, `support`, `billing`, `security`, `noreply` and
similar are addresses a recipient would reasonably read as speaking for
MateMail. Letting a stranger claim any of them hands them the platform's voice.

**Why it sits here and not earlier.** It needs P5's policy framework to have
something to enforce, and P7's operational tooling so abuse is discovered by us
rather than by a blocklist. Opening free signup before both would be launching
the highest-risk surface with the fewest defences, on the sending reputation
every paying customer depends on.

**Why not later than the beta.** The beta should exercise both models. Signup
and username claiming is a different path from domain verification, and
discovering after launch that it was never tested would be the wrong order.

The platform's own sender stays separate: `noreply@mail.matemail.online` is the
transactional identity (DEC-013) and must not be collapsed into the free-user
domain.

---

### P8 — MateMail webmail

> **Superseded by P11 — PostBox.** This section is the plan as written
> before the work; see *P11 — MateMail PostBox* at the end of this document
> for what was actually built and what remains unproven. Two things changed
> in the doing: the hostname is `postbox.matemail.online`, not
> `webmail.matemail.online`; and the SSO bridge was removed rather than
> completed, because it could mint a login token for any mailbox in a
> tenant (DEC-049).

**Not a launch blocker.** May be built before or after P9. Until it exists,
customers use standard IMAP/SMTP clients (DEC-005r).

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

### P9 — Public launch readiness

Gates the **broader public and commercial launch**. The private beta has already
run by this point, so this phase is about being ready for customers who did not
arrive through a personal introduction.

- Broaden test coverage past P0's security core and P1's boundary contract.
- Public front door: landing, pricing, security and docs pages. Today `/`
  redirects straight to `/login`, so there is nowhere to send a prospect.
- Billing: no payment path exists — plans carry prices but nothing can be paid.
- Runbooks: blocklist removal, restore, engine upgrade, incident response.
- Self-service onboarding that survives a customer with no hand-holding.
- Capacity and rate planning against Contabo's ~25 emails/minute policy, sized
  for the signup rate an open front door produces.
- Fold every private-beta incident back into tests, runbooks or product.

**Exit:** signups can be opened without a human in the loop; a prospect can
evaluate, buy and onboard unaided; every beta incident has a documented
resolution.

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
- **No customer mail before P7 is complete.** P2–P7 all land before the private
  beta. The operational controls in P7 — monitoring, reconciliation, queue and
  storage visibility, alerting — are what make it responsible to hold someone
  else's mail, so they are a prerequisite rather than a follow-up.
- **P8 is the largest phase and the least urgent.** The Email Clients page in P4
  makes the product usable without it, and webmail must not block the public
  launch unless that decision is explicitly revisited in `docs/DECISIONS.md`.
- **P6 is a beta prerequisite, not a post-launch task.** Simulated backups plus
  one-call irreversible deletion is the combination that turns a bad week into a
  closed business.

---

## Production Readiness Phase 1 — Harden the Mail Engine boundary

**Completed 2026-09-11.** Scope: make `MailEngineAdapter` a clean, secure
internal boundary. No engine deployed, no VPS access, no migration.

### The port now

| Before | After |
|---|---|
| `ProvisionResult(success, message, raw)` | Methods return `None` or a DTO; failure raises a typed error. One error channel, not two. |
| Django model instances as arguments | Frozen DTOs in `apps/mail_engine/dto.py` |
| Engine response dict returned via `.raw` | Nothing engine-shaped crosses outward |
| `provision_*` (create semantics) | `ensure_*` (idempotent upsert), contract documented per method |
| Product logic inside the adapter | `apps/forwarding/services.py` resolves the destination set |
| Engine text substituted by a term list | MateMail-authored messages on the exception types |

New capabilities: `list_domains`, `list_mailboxes`, `get_mailbox_usage`,
`get_last_login`, `rotate_dkim_key`, `check_health`.

### Error taxonomy

`apps/mail_engine/errors.py`: `MailEngineError` (base) plus `EngineUnavailable`,
`AlreadyExists`, `NotFound`, `Rejected`, `QuotaExceeded`.

Two properties make them safe to handle carelessly:

1. **`str(exc)` is the customer message.** Technical detail lives in
   `.technical_detail`, never in `__str__`. The old L1 anti-pattern
   (`field = str(exc)`) is therefore harmless if it ever recurs.
2. **`customer_message` is authored by MateMail**, never derived from engine text.

Callers branch on type: `EngineUnavailable` is retried, an explicit rejection is
terminal and recorded for the customer.

### Idempotency

Documented per method in `adapter.py` and enforced by the contract suite.
`ensure_*` are upserts; `delete_*` treat "already absent" as success;
`set_*` are assignments. `rotate_dkim_key` is explicitly **not** idempotent and
must never run from a retry path.

One subtlety worth keeping: `ensure_mailbox` applies a password only when one is
supplied, so a retry that omits it cannot clear a working credential.

### DKIM (DEC-007r)

The port exposes only public material. `rotate_dkim_key` asks the engine to
generate the keypair and returns `DkimKeyInfo`, which has no private field —
and a test walks every DTO asserting none may ever gain one.

`Domain.dkim_private_key` still exists and is untouched: migrating it belongs to
P4, as specified. It is marked deprecated in the model.

### Customer-facing naming

`mail_engine_provisioned` / `mail_engine_error` are serialized as
`mail_service_ready` / `mail_service_message`. Database columns are unchanged,
so there is no migration. Frontend strings like "Provisioned in mail engine"
became "Mail service active": the old wording told customers a separate engine
exists, which DEC-011 forbids.

### Health

`/api/health/` now returns `{status, service}` and nothing else. It no longer
consults the engine at all, so mail-side trouble cannot pull the web tier out of
a load balancer, and the probe cannot be used to poll engine state from outside.
Component detail moved to `/api/internal/health/` behind the shared secret.

### Behaviour changes worth knowing

Failures that were previously swallowed now surface, because silently reporting
success was misreporting the customer's mail flow:

- Forwarding create returns **202** (not 201) when the engine is unreachable —
  the rule exists but is not live.
- Forwarding delete returns **503** rather than deleting our record while the
  engine still forwards mail to a third party.
- Forwarding/alias status changes roll back and return **503** if the engine
  rejects them.
- Queue cancel and quarantine release return **503** instead of marking a
  message actioned that was not.

### Tests added (137 new, 244 total)

| File | Tests | Covers |
|------|-------|--------|
| `tests/test_adapter_contract.py` | 79 | one contract run against **both** adapters (39 shared + 7 error-mapping) |
| `tests/test_forwarding_service.py` | 19 | rule resolution, adapter purity, API behaviour on failure |
| `tests/test_dto_boundary.py` | 16 | DTOs only, no models across the port, task retry semantics |
| `tests/test_engine_leak.py` | 12 | 14 realistic engine failures × response bodies and stored fields |
| `tests/test_health_privacy.py` | 11 | public payload minimal; detail behind the secret |
| `tests/fake_engine.py` | — | in-memory fake of the engine REST API |

`MailcowAdapter` is exercised against a fake transport rather than mocked, so
payload construction, status handling, envelope parsing and error
classification all run for real.

**Two real bugs the contract suite caught while being written:**

1. `list_mailboxes(domain)` used `/get/mailbox/{domain}`, which addresses a
   single mailbox. Domain scoping needs `/get/mailbox/all/{domain}`. This would
   have failed against a real engine.
2. The stub's DKIM rotation used a microsecond timestamp, so two rotations in
   the same microsecond returned identical keys.

### Known limitations carried forward

- `Domain.dkim_private_key` still holds plaintext keys (L9) until P4.
- `WEBMAIL_BASE_URL` may still point at an engine-supplied interface (L6) until P8.
- `get_queue_status` / `get_quarantine_items` remain engine-wide with no tenant
  dimension. The port documents that callers must map to a tenant before storing
  or displaying; the sync task that does so is P7.
- The engine-side policy path is built and tested in the repository (blocker 2
  closed in code) but is **not yet deployed**, so the running engine does not
  consult MateMail's decisions at submission time. No public mail port is open,
  and the engine's own SASL, sender-login and relay restrictions are holding.
  See "P5" above.

---

## Production Readiness Phase 2 — Deployment alignment

**Completed 2026-09-11.** Scope: bring the deployment architecture in line with
the NetaMate production standard. **Nothing was deployed**; MateServer was not
touched; the Mail Engine was not installed.

### Port allocation (final)

| Service | Host binding |
|---|---|
| backend | `127.0.0.1:8020` |
| frontend | `127.0.0.1:3020` |
| postgres | none — internal network only |
| redis | none — internal network only |

`8015`, `8016`, `3015` belong to MateConnect and are referenced nowhere in the
repository any more. Development uses 8020/3020 too, so local and production
URLs line up.

### Production architecture

`deploy/docker-compose.yml` is self-contained and copied to
`/opt/MateMail/docker-compose.yml`. No build context, no bind mounts, no
repository checkout on the server.

- GHCR images, SHA-pinned, selected by `MATEMAIL_BACKEND_IMAGE` and
  `MATEMAIL_FRONTEND_IMAGE`. Both declared `:?` so a missing value fails the
  command instead of starting something unintended.
- `celery-worker` and `celery-beat` reuse the **backend image** with a different
  command; the previous compose built the same image three times.
- A one-shot `migrate` service runs to completion (`service_completed_successfully`)
  before backend, worker and beat start.
- Named volumes for postgres and redis; `restart: unless-stopped` everywhere;
  healthchecks on every long-running service.
- **Two networks, minimum membership per service.** `matemail_internal` is
  declared `internal: true`, so Docker installs no route off the host for it.
  Postgres and Redis are attached *only* there. `matemail_app` is a bridge
  providing the outbound access the app tier needs. Verified live: postgres,
  redis and beat cannot reach 1.1.1.1; backend and celery-worker can; all of
  them still reach postgres and redis.

  | Service | Networks | Why |
  |---|---|---|
  | postgres, redis | `matemail_internal` | no outbound, ever |
  | migrate | `matemail_internal` | schema changes touch the DB only |
  | celery-beat | `matemail_internal` | reads the schedule, enqueues; never executes a task |
  | backend, celery-worker | both | DB/Redis **plus** outbound for email, DNS and (P4) the engine |
  | frontend | `matemail_app` | client-rendered; never touches a datastore |

  An earlier draft named a network `internal` but declared `driver: bridge`,
  which is not isolation at all. The CI compliance check now asserts
  `internal: true` explicitly rather than trusting the name.
- **Celery beat healthcheck uses only shell builtins.** `--pidfile` plus
  `test -f` and `kill -0`. The image is `python:3.11-slim`, which ships
  neither `pgrep` nor `ps`, so the earlier `pgrep -f` check could never
  succeed and would have reported a healthy beat container as unhealthy forever.
  Verified inside a running beat container and confirmed by Docker'"'"'s own
  health verdict.

### CI/CD

Two workflows replace the single deploy-on-push one:

- **`ci.yml`** — on push/PR: backend `check`, `check --deploy`,
  `makemigrations --check`, full test suite against real Postgres and Redis;
  frontend `npm ci`, lint ratchet, production build; production compose
  validation including a script asserting no host ports on datastores, no build
  contexts, no bind mounts and no MateConnect ports. Publishes SHA-pinned GHCR
  images **only** after all gates pass, and never from a pull request.
- **`deploy.yml`** — `workflow_dispatch` only. **No push trigger exists.**

Publishing an image is not a deployment: images wait in GHCR until an operator
runs the deploy workflow deliberately.

### Lint ratchet

The 18 pre-existing `react-hooks/set-state-in-effect` errors are not hidden and
not fixed. CI prints the count every run and fails only if it **rises** above
`ESLINT_ERROR_BASELINE=18`, and emits a notice if it falls. New violations are
blocked; known debt does not make the pipeline permanently red. Fixing the 18
means restructuring effects across several pages — frontend rework outside P2.

### Bug found while verifying the Dockerfile

`STATICFILES_STORAGE` was **removed** in Django 5.1, not merely deprecated —
`django.conf.global_settings` has no such attribute on 5.1.4. The project's
`STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"`
was therefore **silently inert**, and Django fell back to plain
`StaticFilesStorage`. WhiteNoise's compression and content-hashing had never
run in any environment.

Fixed by moving to `STORAGES`, with the manifest backend in `prod.py` only —
manifest storage refuses to resolve a file absent from `staticfiles.json`, so
enabling it in `base.py` would make `manage.py test` and local development
depend on `collectstatic` having been run.

Verified in the built image: manifest present, 25 hashed CSS files, 306 gzip
variants. This also corrects the P0 note that predicted runtime 500s from a
missing manifest — the outcome (static worked) was right, the mechanism was not:
the storage backend was never active to fail.

### Removed

| Removed | Why |
|---|---|
| `docker-compose.prod.yml` | An override of the dev base, so it required a checkout on the server. Replaced by the standalone `deploy/docker-compose.yml`. |
| `docker-compose.mailengine.yml` | An nginx container returning a fake `{"status":"stub"}` on `/api/v1/info` — a path P1 no longer even calls. Misleading placeholder, not infrastructure. The real engine arrives in P4. |
| `nginx/mailengine-stub.conf` | Served the fake payload above. |
| `nginx/conf.d/matemail.conf`, `nginx/conf.d/matemail-tls.conf`, `nginx/nginx.conf` | A containerized nginx model with Docker-DNS upstreams. No nginx service existed in any compose file, and DEC-011 makes host-native nginx the only public proxy. |
| `nginx/matemail-vhost.conf` | Superseded by the Workspace vhost — then `deploy/nginx/app.matemail.online.conf`, now `deploy/nginx/portal.matemail.online.conf` (correct ports, CSP, `/api/internal/` deny). |
| `scripts/init-letsencrypt.sh` | Bootstrapped certs for nginx and certbot containers that do not exist. The host's certbot owns certificates. |
| `scripts/deploy.sh` | Built from source on the server and interpolated an admin password into a shell string. Replaced by the deploy workflow. |

`scripts/postfix_policy_bridge.py`, its systemd unit and
`scripts/apply-mailcow-config.sh` were **kept** — they are real P5 artifacts, not
placeholders. Their `DJANGO_INTERNAL_URL` default was corrected to port 8020.

### Files added

`deploy/docker-compose.yml`, `deploy/env.production.example`,
`deploy/nginx/app.matemail.online.conf` (renamed to
`portal.matemail.online.conf` in P11), `deploy/README.md`,
`.github/workflows/ci.yml`.

### P1 architecture preserved

Untouched: the adapter boundary, DTO contract, typed errors, customer-facing
error protection, `/api/internal/` protection, tenant isolation, DEC-007r. The
244-test suite passes unchanged.

### Still required before MateServer can be touched

1. **P4** must stand up the Mail Engine. `MAIL_ENGINE_ADAPTER` stays `stub` until then.
2. Host bootstrap, once, by hand: `/opt/MateMail/` with `docker-compose.yml` and
   a `0600` `.env`, `docker login ghcr.io`, and the nginx vhost installed and
   reviewed. See `deploy/README.md`.
3. GitHub secrets configured: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PORT`,
   `GHCR_TOKEN`.
4. A required reviewer set on the `production` GitHub environment.
5. Outbound TCP/25 verified from MateServer itself.
6. Per **DEC-012**, no real customer mail until all of P0–P7 are complete.

---

## P2.5 — Controlled production deployment of the control plane

> **Hostname since renamed.** Everything below is the deployment as measured
> on 2026-09-10 and is left unedited. The customer console is now the
> MateMail Workspace at `portal.matemail.online`; `app.matemail.online` is a
> legacy 308 redirect (DEC-055). The certificate names and expiry recorded
> here are the ones issued then, not the current ones.

**Completed 2026-09-10.** The MateMail **control plane** is live on MateServer at
<https://app.matemail.online>. The Mail Engine was **not** deployed;
`MAIL_ENGINE_ADAPTER=stub` and no SMTP/IMAP/POP/Sieve listener exists.

Per **DEC-012** this is *not* the private beta: P3–P7 remain, and no real
customer mail may reach the platform until they are complete.

### Deployed artifact

| | |
|---|---|
| Commit | `57afda7c1ebc0efde418d3c6a36dbe2a9a334c60` |
| Backend image | `ghcr.io/rizwansammo/matemail-backend@sha256:edbd7ca9…` |
| Frontend image | `ghcr.io/rizwansammo/matemail-frontend@sha256:b54d537a…` |
| CI run | 34522742832 — all four jobs green |

### Host layout

```
/opt/MateMail/
├── docker-compose.yml   0644  (byte-identical to deploy/docker-compose.yml at the deployed commit)
├── .env                 0600  root:root, secrets generated on the server
└── backups/             0750
```

No git checkout on the server. No build context. No source bind mount.

### Ports

`127.0.0.1:8020` backend, `127.0.0.1:3020` frontend. Both were verified free
before deployment by `ss` and by Docker port inspection. PostgreSQL and Redis
publish no host port. No UFW change was needed or made — MateMail binds
loopback only and is reached through the existing host nginx.

### Routing

Host-native nginx site `/etc/nginx/sites-available/matemail`, its own symlink,
no other site touched (checksums verified identical before and after).

- `app.matemail.online/api/` → `127.0.0.1:8020`
- `app.matemail.online/` → `127.0.0.1:3020`
- `app.matemail.online/api/internal/` → **403 at the edge**
- `matemail.online` and `www.matemail.online` → 301 to `app.matemail.online`
  (own server block, so the P9 marketing site can replace it without touching
  the app vhost)

TLS: Let's Encrypt `matemail.online` covering **`matemail.online`,
`www.matemail.online` and `app.matemail.online`**, expiring 2026-12-09, issued
by the host's existing certbot. `webmail.matemail.online` deliberately excluded
— it has no A record and nothing to serve.

**Port-80 block restructured when www was added.** Certbot had left it as
`if ($host = …) { return 301 }` plus a server-level `return 404`. Both run in
nginx's *server* rewrite phase, which completes before a location is selected,
so an ACME challenge for any name without a matching `if` would 404 and
issuance for a new name would fail. Replaced with a location-based form: the
challenge path has its own `location`, everything else redirects. Renewal
dry-run for all three names succeeds. `certonly` was used for the expansion so
certbot authenticated via nginx without rewriting the config back.

### Known-unavailable, deliberately

- **Transactional email is not configured.** `EMAIL_HOST=localhost`, where
  nothing listens, so application mail fails rather than appearing to work.
  Because P0 gates domain and mailbox provisioning on a verified address, and
  verification mail cannot be delivered, **self-service onboarding cannot
  complete**. A platform admin can set `email_verified` manually meanwhile.
  Configuring a real provider is a P3 item.
- **Mail Engine absent.** Domain and mailbox actions run against the stub: they
  succeed in MateMail's database and provision nothing real.

### Pre-existing DNS finding (not created by this phase)

`matemail.online` already carries `MX 10 mx.matemail.online`, and
`mx.matemail.online` resolves to MateServer where port 25 is closed. Mail sent
to the domain therefore fails today. Nothing was changed — mail DNS is out of
scope until P4 — but the record advertises a service that does not exist and
should either be removed until P4 or retained knowingly.

### Post-deployment fix: CSP blocked Next.js hydration

The first browser visit showed only the "Loading MateMail…" shell. Cause: the
CSP added in P2.5 used `script-src 'self'`, but the Next.js App Router emits
**6 inline `<script>` tags** carrying the hydration payload (`self.__next_f`).
All were blocked, so React never hydrated and the static shell never advanced.
`curl` never caught it because the HTML and the JS chunks both return 200 — only
a real browser executes the CSP.

**Fix applied:** `'unsafe-inline'` added to `script-src` in the nginx site.
Verified afterwards: `/` and `/login` load, all JS chunks fetch, the bundle
carries the correct same-origin API base, `POST /api/auth/login/` returns a
proper 401, and the preflight succeeds.

**This is a real weakening, not a cosmetic one.** `'unsafe-inline'` on
`script-src` removes CSP's main protection against injected script, and refresh
tokens still live in `localStorage` (also P3). Together those are exactly the
pair that makes an XSS costly.

**Proper fix is P3:** a per-request nonce issued by Next.js middleware, with
nginx no longer setting the CSP header for the frontend. That needs a code
change, a CI build and a redeploy, so it could not be done from the server.
Until then this is tracked as open security debt.

### Deployment gates

`workflow_dispatch` only and the `MateServer` confirmation input are both
active. The `production` environment exists, but **required reviewers are a
paid feature for private repositories**, so that gate is present and not
enforced on the current plan. `deploy.yml` records this.

`deploy.yml` was changed to authenticate GHCR with the job-scoped
`GITHUB_TOKEN` (`packages: read`) instead of a long-lived `GHCR_TOKEN` secret.
At the time of that deployment the change was not yet committed, so the
workflow-based deployment path was not exercised; this deployment was performed
over SSH under P2.5 authorization.


---

## P3a — Domain ownership verification, provisioning gates, async DNS

**Date:** 2026-09-11 **Status:** implemented; committed in `5e20154`.
**Not deployed** — no MateServer change, no mail DNS change and no Mail Engine
install was performed in this phase.

P3 was split into three parts to keep each reviewable. P3a covers brief §1
(CI/CD cleanups), §2 (domain ownership verification) and §7 (asynchronous DNS
checking). P3b and P3c are not started.

### The defect this closes

Adding a domain queued a mail-engine provisioning task immediately. Nothing
checked that the person adding `competitor.example` controlled it. That is
blocker #6 in the readiness summary, and it is the one abuse path that would
have been visible to an outsider.

### Ownership model

`Domain` gained `ownership_status` (`pending` | `verified`),
`verification_token`, `ownership_verified_at`, `verification_last_checked_at`
and `verification_last_error`. The design is in `docs/SECURITY.md` §Domain
Ownership Verification; the two decisions worth repeating here:

**Exclusivity is a database constraint, not a code path.** The old global
`unique=True` on `domain` was wrong in both directions: it stopped two tenants
from even attempting the same domain, while providing no ownership meaning. It
was replaced by two constraints — `uniq_domain_per_tenant` (a tenant lists a
domain once) and a **partial unique index** on `domain WHERE ownership_status =
'verified'`. Several tenants may hold a domain as pending; exactly one can hold
it verified. The concurrent-verification race is closed by Postgres, because a
check-then-save in Python cannot close it.

**Matching is exact.** The check compares the token to each returned TXT string
for equality, after reassembling multi-string records. A substring match would
verify against an unrelated record in a shared zone that happens to contain the
token.

### Provisioning gates

| Layer | Unverified behaviour |
|-------|----------------------|
| `POST /api/domains/` | domain created, token issued, **nothing queued** |
| `POST /api/domains/:id/provision/` | `409` + customer message |
| `POST /api/mailboxes/` | `409`, no row created |
| `POST /api/mailboxes/:id/reprovision/` | `409` |
| `provision_domain_task` | refuses at the task boundary, records the reason, no retry |

The task-boundary check is the durable one: a future `.delay()` caller that
skips the view still cannot provision an unclaimed domain.

### Asynchronous DNS checking (§7)

`POST /api/domains/:id/check/` performed up to four resolver lookups at a 5s
timeout **inside the request**. One unresponsive nameserver held a gunicorn
worker for ~20s; sixteen concurrent checks took the API offline. It now claims
a per-domain slot, enqueues, and returns **202** with the last known state
explicitly labelled as such — it never fabricates a fresh verdict.

The periodic sweep fanned out serially, so one slow domain delayed every domain
behind it. It now dispatches one task per domain, guards each dispatch so a bad
row cannot abort the sweep, and returns `{enqueued, skipped, failed, total}`.

Deduplication uses atomic `cache.add`: a 60s in-flight slot collapses repeated
clicks and stops a sweep piling onto a manual check, and a 300s budget key
enforces the 1-per-5-minutes background limit that `docs/SECURITY.md` already
specified. A crash always releases the slot, so a failure cannot wedge a domain
into a permanently un-checkable state.

### CI/CD cleanups (§1)

- **`run_migrations` input removed.** Migrations are mandatory: the compose
  `migrate` service gates the others via `service_completed_successfully`, so
  the input could not actually skip them. A control that does not control is
  worse than no control.
- **`docker image prune` removed.** MateServer also runs NetaMate, TalkRoom,
  MateDesk, MateAssist, MateConnect and Portfolio. A host-global prune from a
  MateMail deploy could delete an image another application needs to recreate a
  container.

Both are now asserted by tests rather than only by review.

### Frontend

- New `components/domain-ownership.tsx` renders the exact **Type / Host /
  Value** with per-field copy buttons, the short host form for providers that
  append the zone, the last check result, and the verify / rotate actions.
- The onboarding wizard's steps are re-ordered to `Workspace → Domain → Verify
  → DNS → Mailbox → Complete`. Mailbox creation is disabled until ownership is
  verified, with the reason stated — previously the customer would have met a
  bare `409` at the end of the wizard.
- The mailbox form rendered `detail` and `local_part` errors but **not
  `domain_id`**, which is the key the ownership refusal uses; the form would
  have appeared to do nothing. Now rendered, and the domain picker labels
  unverified domains.
- Both DNS-check callers were rewritten for the 202 contract: they report that
  the check started and re-read the real result, instead of presenting the
  endpoint's last-known payload as this run's outcome.

### Migrations

| Migration | Effect |
|-----------|--------|
| `domains/0003_domain_ownership_verification` | Adds the five ownership fields; **drops** the old global unique on `domain`; adds `uniq_domain_per_tenant` and the partial `uniq_verified_domain_owner` |
| `domains/0004_backfill_verification_tokens` | Issues a token to every existing row and leaves them all `pending`; reversible |

Verified against a real Postgres 16 instance rather than trusting `sqlmigrate`
(which did not show the constraint drop). `\d domains_domain` confirms the old
global unique is gone and both new constraints exist.

**Existing domains become `pending` on deploy.** That is deliberate — none of
them were ever proven — but it means any domain already added must be verified
before it can be provisioned. No production customer domains exist yet, so the
backfill affects nothing live.

### Validation

| Check | Result |
|-------|--------|
| Backend suite | **312 passed** (244 pre-existing + 68 new) |
| `makemigrations --check` | No changes detected |
| `manage.py check` | No issues |
| `check --deploy` (prod settings) | 1 warning, `security.W019`, pre-existing: `X_FRAME_OPTIONS = "SAMEORIGIN"` is set deliberately in `config/settings/prod.py` |
| Frontend `tsc --noEmit` | Clean |
| Frontend lint | 35 problems (17 errors, 18 warnings) — **unchanged baseline**; no problem is on a line this phase introduced, and the two new/rewritten files report none |
| Frontend production build | Succeeds, 28 routes |
| `docker compose config` | Valid; `matemail_internal` still `internal: true`, only 127.0.0.1:8020 and 127.0.0.1:3020 published, no mail ports, no `:latest`, `migrate` gate intact |

New tests: `tests/test_domain_ownership.py` (37), `tests/test_dns_async.py`
(19), `tests/test_deployment_workflow.py` (12).

### Not done in P3a

Deliberately deferred, not forgotten: rate limiting, login lockout and 2FA
replay, workspace/plan caps and API key scopes (P3b); refresh-token cookies,
CSP nonces, transactional email and interim DKIM encryption (P3c).


---

## P3b — Abuse limits, login and 2FA hardening, plan caps, API key scopes

**Date:** 2026-09-11 **Status:** implemented; committed in `5e20154`.
**Not deployed** — no MateServer change, no mail DNS change and no Mail Engine
install was performed in this phase.

Covers brief §3 (rate limiting), §4 (login lockout and 2FA replay), §5
(workspace and plan abuse controls) and §6 (API key scopes). P3c is not started.

### Preflight findings

Verified against source before writing anything. Six defects, two of them
larger than the brief anticipated:

1. **Every per-IP limit was bypassable.** `NUM_PROXIES` was unset, so DRF's
   `get_ident` used the whole `X-Forwarded-For` string as the throttle key. An
   attacker varying the header got a fresh bucket per request. The audit log
   had the same flaw from the other end — it recorded the *leftmost* entry, so
   `MailLog.ip_address` was attacker-chosen.
2. **`AuthThrottle` was an `AnonRateThrottle`**, which returns immediately for
   an authenticated request. `resend-verification`, 2FA enrolment and 2FA
   disable were therefore entirely unlimited.
3. **`check_member_limit` was never called.** `Plan.max_members` was a number
   in the database that no code path consulted.
4. **Workspace creation had no cap.**
5. **API keys authenticated as their creator**, inheriting every permission
   that user held — `is_platform_admin` included.
6. **`/api/internal/smtp/` was under the 60/min anon throttle**, which would
   have capped the Mail Engine policy bridge at 60 lookups a minute.

And one found while writing the tests, larger than any of the above:

7. **API keys had never authenticated at all.** `JWTAuthentication` was first
   in `DEFAULT_AUTHENTICATION_CLASSES`, and simplejwt claims every `Bearer ...`
   header and *raises* `InvalidToken` rather than deferring, so every `mm_` key
   got 401 before `APIKeyAuthentication` ran. `TenantMiddleware` bailed in the
   same `except` for the same reason, so no tenant was resolved either. The
   feature had a model, a UI, a serializer and an endpoint, and did not work.
   Confirmed against `HEAD` — not introduced by this phase.

### §3 Rate limiting

New `apps.security` package: `client_ip` (trusted-proxy address resolution),
`ratelimit` (atomic `cache.add`/`incr` over wall-clock fixed windows), `limits`
(every number in one place, read by the views, the tests and the docs), and
`throttling` (DRF classes keyed on the real client address, exempting
`/api/health/` and `/api/internal/`).

`TRUSTED_PROXY_COUNT` is 0 by default and **1 in production**, matching the one
nginx hop. nginx appends the address it saw, so the rightmost entry is the only
one our own infrastructure wrote.

Limits are in `docs/SECURITY.md` §Rate Limiting. Login counts **failures** and a
correct password clears the counters — counting every attempt would let one
person on a shared NAT lock out an office while adding nothing against brute
force. Refusals are 429 with `Retry-After`, raised through DRF's `Throttled`.

`django-ratelimit` was evaluated and not adopted: its exception maps to 403 not
429, and its IP keying has the same untrusted-header problem that had to be
solved here anyway. This is a deliberate deviation from the brief's suggested
dependency, which conditioned it on being the appropriate choice.

### §4 Login and 2FA

Phase 0's challenge-token design is untouched and now has regression tests
pinning each of its properties. Added on top:

- Per-IP and per-account login lockout, with one message for every refusal —
  "wrong password", "no such account", "disabled" and "locked" are each an
  oracle if they can be told apart. The disabled-account branch previously
  returned a distinct message and no longer does.
- A per-user 2FA budget spanning every challenge that user holds. The Phase 0
  counter caps attempts against one token; an attacker with the password just
  requests another after every fifth failure.
- **TOTP replay prevention.** With `valid_window=1` a code is accepted for
  ninety seconds. An accepted code is now claimed for that user until it
  expires, so an observed code works exactly once.
- Limits on 2FA enrolment and disable, which had none.

### §5 Workspace and plan caps

`MAX_WORKSPACES_PER_USER` (default 5, env-overridable) counts workspaces the
user **owns** — being invited into other people's is not abuse. `max_members`
is now enforced on direct member creation, member reactivation, invite creation
and invite acceptance.

**Pending invitations reserve a seat**, which is the documented decision.
Counting only accepted members lets a three-seat workspace send thirty
invitations, each acceptance individually passing a count taken before the
others landed. Every check runs inside the transaction that takes the seat with
the tenant row locked; workspace creation locks the user row, because the row
being counted does not exist yet.

### §6 API key scopes

Five scopes, resource shaped: `read` (always held), `domains:write`,
`mailboxes:write`, `routing:write`, `admin`. A new key is read-only, and
**being created by an owner or admin grants it nothing** — write scopes must be
named. Corrupt or empty stored scopes degrade to read-only.

Enforced by `APIKeyScopeMiddleware` rather than a DRF permission class, because
DRF *replaces* `DEFAULT_PERMISSION_CLASSES` for any view declaring its own —
which is nearly every view here — so a global default would enforce nothing.
It is default-deny: an unmapped mutating path is refused, so a future endpoint
is closed to keys until somebody maps it.

`/api/platform/`, `/api/internal/` and `/api/auth/` are unreachable by any key
at any scope. `IsPlatformAdmin` refuses API-key requests as well, so the rule
survives an endpoint moving out of that prefix. Creation, revocation and scope
changes are audited.

### Migrations

| Migration | Effect |
|-----------|--------|
| `teams/0002_apikey_scopes` | Adds `scopes` (JSONB). Existing rows default to `["read"]` |

**Behaviour change on deploy:** every existing API key becomes read-only. Any
integration writing through one starts receiving 403 until its scopes are
granted. That is the intended direction — grandfathering write access in would
preserve exactly the privilege this change removes. In practice no key has ever
worked (finding 7), so nothing real is affected.

### Frontend

The API keys page now picks scopes at creation, shows them per key, and edits
them in place. Its footer previously read *"API keys carry admin-level
access"* — true of the old behaviour, false now, and removed.

No workspace-creation UI exists yet, so the workspace cap has no frontend
surface to update. The team page already renders `detail`, so the seat-limit
refusal displays without a change.

### Validation

| Check | Result |
|-------|--------|
| Backend suite | **415 passed** (312 pre-existing + 103 new) |
| `makemigrations --check` | No changes detected |
| `manage.py check` | No issues |
| `check --deploy` (prod settings) | 1 warning, `security.W019`, pre-existing and deliberate |
| Frontend `tsc --noEmit` | Clean |
| Frontend lint | 34 problems (17 errors, 17 warnings) vs a 35-problem baseline — same files, same rules, **one fewer** warning; nothing new |
| Frontend production build | Succeeds, 28 routes |
| `docker compose config` | Valid; `internal: true` intact, only 127.0.0.1:8020 and 3020 published, no mail ports, no `:latest` |
| Prod settings spot-check | `TRUSTED_PROXY_COUNT=1`, `MAX_WORKSPACES_PER_USER=5`, throttle rates and authenticator order as intended |

New tests: `test_rate_limits.py` (35), `test_2fa_hardening.py` (16),
`test_plan_caps.py` (18), `test_api_key_scopes.py` (34).

`tests/factories.py` — `disable_throttling` derived its scope list from a
hardcoded dict, so it silently stopped covering throttles the moment a scope
was added. It now reads the live configuration.

### Documentation corrected, not just extended

`docs/SECURITY.md` claimed password hashing was "Argon2 via django-argon2".
`PASSWORD_HASHERS` is not configured and `argon2-cffi` is not a dependency, so
it is Django's default PBKDF2. The table now says so, and marks
"suspicious login alerts" and "session listing" as not built. The IMAP/SMTP
table is marked as target design for an engine that is not installed, and its
"Stalwart" references — which predate DEC-001 — are gone.

### Carried forward, not done here

Moving to Argon2 is a settings line plus a dependency; it is hardening, not
P3b's scope, and is now stated honestly rather than claimed. §7 was done in
P3a. §8–§11 and §13 are P3c.


---

## P3c — Token storage, CSP nonces, transactional email, DKIM at rest

**Date:** 2026-09-11 **Status:** implemented; committed in `5e20154`. One item
is explicitly NOT production-complete (transactional email — see below).
**Not deployed** — no MateServer change, no mail DNS change and no Mail Engine
install was performed in this phase.

Covers brief §8 (refresh token storage), §9 (CSP nonces), §10 (transactional
email), §11 (interim DKIM encryption) and §13 (documentation).

### §8 Refresh token storage

The refresh token was returned in the JSON body and kept in `localStorage`. It
is the durable credential — seven days, and it mints access tokens for all of
them — so one XSS bought a week of silent access.

It is now an `HttpOnly; Secure; SameSite=Strict` cookie scoped to
`/api/auth/`, with `Max-Age` matching the token's own lifetime. Strict rather
than Lax because the refresh happens by same-origin XHR after load, so the one
thing Lax buys does not apply here.

**CSRF is avoided rather than accepted.** The API is not cookie-authenticated:
every endpoint still requires a bearer access token in a header, which a
cross-site request cannot set. The cookie authorises exactly one operation, the
token exchange, and SameSite=Strict already prevents any cross-site request
carrying it.

There is exactly one mechanism — a token supplied in the request body is
rejected, because two mechanisms means the weaker one defines the security of
the pair. Login, signup, 2FA verify, refresh, logout, password reset, workspace
switch and workspace create were all audited and all covered by tests.

Browsers holding a pre-P3c `localStorage` token have it deleted on first load
of the new build.

**Residual exposure, stated rather than hidden:** the access token is still
JavaScript-readable in `sessionStorage`. An XSS can act as the user for up to
15 minutes and cannot extend that, because it cannot reach the refresh token.

### §9 CSP nonces — and the trap that was nearly shipped

`frontend/middleware.ts` issues a per-request nonce; nginx no longer sets CSP
for frontend responses, because two CSP headers are enforced as an intersection
and nginx cannot know the nonce.

**The part worth reading.** The first working version produced a correct-looking
header with no `unsafe-inline` — and the served HTML had **19 script tags and
0 nonce attributes**. Next.js only stamps nonces onto routes it renders at
request time, and the pages were statically prerendered at build time. Shipping
that would have reproduced the P2.5 dead-shell failure, except worse: the
policy would have been actively blocking the scripts. `app/layout.tsx` now sets
`dynamic = "force-dynamic"`. The cost is static optimisation for the marketing
page; everything else was an authenticated client-rendered dashboard already.

Verified in a real Chromium against a production build, not from curl:
**17/17 checks** — header shape, no `unsafe-inline`, no `unsafe-eval`, one
policy not two, nonce present and per-request, client runtime booted, React
attached, form updates state, client-side navigation works, unauthenticated
root reaches the login screen, zero CSP violations, zero blocked
script/stylesheet/document requests. The harness is kept at
`frontend/scripts/csp-browser-check.js` and can be pointed at the deployed site
after release.

nginx now sets CSP only where it is the origin: `default-src 'none'` for
`/api/` and `/static/`, and a policy retaining `'unsafe-inline'` for
`/django-admin/`, which ships inline scripts and has no nonce mechanism. The
`/static/` block already carried its own `add_header`, which under nginx's
rules had silently dropped every inherited security header from those
responses; they are restated. `nginx -t` passes (validated in a container).

### §10 Transactional email — NOT production-complete

Two defects fixed. Every send used `fail_silently=True`, so the API answered
"Verification email sent." having sent nothing, with no trace anywhere. And
`EMAIL_HOST` defaulted to `localhost`, where nothing listens — and if anything
ever did, it would be the Mail Engine, which is exactly where application mail
must not go.

`apps.accounts.mailer` reports whether a message was accepted and logs the
cause when it was not. Where an honest answer carries no risk it is given:
`resend-verification` returns 503 on failure, invite creation reports
`email_delivered: false`. The password-reset response deliberately does not
change, because a different answer for a registered address is an existence
oracle.

**This item is deliberately NOT marked production-complete.** The application
path is built and tested; no real delivery test has been performed.

*Superseded by DEC-013:* delivery is MateMail's own Mail Engine, not an external
provider, so no provider account or credentials will be created. The test moves
to the first suitable P4 milestone. No MX/SPF/DKIM/DMARC record was created or
changed in this phase.

### §11 DKIM private keys — interim only

DEC-007r is unchanged: P4 moves generation and storage into the Mail Engine and
removes the column. Until then it is encrypted at rest with Fernet under
`DKIM_ENCRYPTION_KEY`, deliberately separate from `DJANGO_SECRET_KEY` — a
`SECRET_KEY` rotation that made every DKIM key undecryptable would be
discovered by customers, as mail failing authentication.

Migration `domains.0005` verifies each value decrypts back to the original
*before* overwriting it, skips already-encrypted rows, and is reversible. Rows
written before the change are read as plaintext transparently, so an upgrade
cannot break signing for existing domains. An unreadable value raises rather
than returning `""`, which would read as "no DKIM key" and provision the domain
to send unsigned mail.

With no key configured, storage degrades to plaintext with an error log rather
than refusing to add domains — and `check --deploy` fails with `domains.E001`,
so that state cannot reach production unnoticed.

### §13 Documentation

`docs/SECURITY.md` gained Token Storage, Content-Security-Policy, Transactional
Email and DKIM Private Key Storage sections. `docs/DEPLOYMENT.md`'s environment
block still showed `EMAIL_HOST=localhost` with a MateMail mailbox — the exact
anti-pattern — and now shows an external provider plus the outstanding setup.
`deploy/env.production.example` and `deploy/docker-compose.yml` carry every new
variable. (The stale Argon2, Stalwart, suspicious-login-alert and
session-listing claims §13 lists were already corrected in P3b.)

### Validation

| Check | Result |
|-------|--------|
| Backend suite | **488 passed** (415 after P3b + 73 new) |
| `makemigrations --check` | No changes detected |
| `manage.py check` | No issues |
| `check --deploy` | 1 warning, `security.W019`, pre-existing and deliberate |
| `check --deploy` without `DKIM_ENCRYPTION_KEY` | Fails with `domains.E001`, as intended |
| Frontend `tsc --noEmit` | Clean |
| Frontend lint | 34 problems (17 errors, 17 warnings) — same as the P3b baseline, same files |
| Frontend production build | Succeeds, 28 routes, middleware registered |
| **Browser CSP + hydration check** | **17/17** in real Chromium against a production build |
| `nginx -t` on the template | Passes (validated in a container with stubbed certs) |
| `docker compose config` | Valid; `internal: true` intact, 2 loopback ports, no mail ports, no `:latest` |

New tests: `test_refresh_cookie.py` (27), `test_transactional_email.py` (21),
`test_dkim_encryption.py` (25).

Two existing tests were updated rather than relaxed: both asserted that the
refresh token comes back in the response body, which is the exposure §8
removes. They now assert the stronger property — that it arrives as an HttpOnly
cookie and is *absent* from the body.

### Carried forward

- **A real transactional delivery test** (above). P3's exit criteria say this
  item is not complete without one.
- **Argon2 password hashing.** Found in P3b, documented honestly rather than
  claimed; it is a settings line plus `argon2-cffi` and was left out as beyond
  the brief's scope. Worth doing before private beta.
- **`security.W019`** — `X_FRAME_OPTIONS = "SAMEORIGIN"` is deliberate, not an
  oversight.


---

## P3 follow-ups — CI fix and Argon2

**Date:** 2026-09-11 **Status:** the CI-key fix and Argon2 are committed in
`4c79c00`; the PyYAML fix below is **not committed**. Not deployed.

Three items raised on review of the pushed P3 commit `5e20154`.

### 1. CI was red on `5e20154`

**Root cause.** P3c made `DKIM_ENCRYPTION_KEY` required in production and CI
never supplied one. Two jobs failed for that single reason, both behaving
exactly as designed:

| Job | Step | Failure |
|-----|------|---------|
| Backend checks and tests | `manage.py check --deploy` | `SystemCheckError: (domains.E001) DKIM_ENCRYPTION_KEY is not set` |
| Validate production compose | `docker compose config` | `required variable DKIM_ENCRYPTION_KEY is missing a value` |

`Publish to GHCR` needs `[backend, frontend, compose]`, so no image was built.
The frontend job passed throughout. Nothing was wrong with the code being
tested — the gate was new and the pipeline had not been told about it.

**Fix.** Both jobs now mint their own key before the steps that need one:

```yaml
- name: Generate an ephemeral CI-only DKIM encryption key
  run: |
    python3 -c "import base64, os; print('DKIM_ENCRYPTION_KEY=' + base64.urlsafe_b64encode(os.urandom(32)).decode())" >> "$GITHUB_ENV"
```

Three properties worth stating:

- **Generated per run, never a repository secret.** A stored secret here would
  be a standing copy of a production-shaped credential that protects nothing —
  CI encrypts no data that outlives the runner.
- **A genuinely valid Fernet key** (32 random bytes, url-safe base64), verified
  locally to round-trip through `Fernet`, so any code path that actually builds
  a cipher from it works rather than merely passing an emptiness check.
- **Standard library only**, so the identical step works in the compose job,
  which never installs the backend's dependencies.

**`domains.E001` was not weakened.** Re-verified after the fix: with the
variable absent, `check --deploy` still exits 1 on `domains.E001` and
`docker compose config` still refuses. Production still fails closed.

### 1b. CI was still red on `4c79c00` — a second, unrelated cause

The DKIM fix worked: production compose, `check --deploy`, the migration check
and the frontend all passed. The backend test job then failed on something the
first fix had been hiding behind it.

```
ImportError: Failed to import test module: tests.test_deployment_workflow
ModuleNotFoundError: No module named 'yaml'
```

**Root cause.** `tests/test_deployment_workflow.py` (added in P3a) parses the
workflow and compose YAML, but `requirements-dev.txt` never declared PyYAML.
The module failed to import, so unittest reported it as one failing test and
**silently never discovered its other 12** — which is why CI counted 495 tests
where the suite has 506. A test that does not run is worse than a missing one:
the count looked plausible and the deployment invariants were unguarded.

**Why the earlier local run did not catch it.** Verified rather than assumed:
PyYAML 6.0.3 was present in the local scratchpad venv with `Required-by:`
**empty** — nothing in the dependency tree pulls it in, so it had been
installed directly at some point and masked the gap. Confirmed from the other
direction too: installing the exact pre-fix dependency set into a bare venv
gives Django 5.1.4 and no `yaml`, and running the suite there reports **495
tests** and reproduces CI's `ModuleNotFoundError` verbatim.

**Fix.** `PyYAML==6.0.2` added to `requirements-dev.txt` only — nothing in the
application imports yaml, so it stays out of `requirements.txt`. A cp311 wheel
exists, so CI's Python 3.11 installs a binary with no compiler.

`PyJWT==2.13.0` was declared at the same time. `tests/test_refresh_cookie.py`
imports `jwt` directly while PyJWT only arrives as a dependency of
djangorestframework-simplejwt. CI does not fail on it today, but a direct
import resting on someone else's transitive dependency breaks silently the day
that library changes its JWT backend — the same bug class, one declarer away
from the same outcome.

An audit of every third-party import across the backend (13 top-level modules,
checked by import in a clean venv) found no others.

### 2. Argon2 password hashing

`PASSWORD_HASHERS` now leads with `Argon2PasswordHasher`, followed by PBKDF2,
PBKDF2SHA1, Scrypt and BCryptSHA256; `argon2-cffi==23.1.0` added to
`requirements.txt`.

The trailing hashers are kept **deliberately**. A password hash cannot be
converted to another algorithm without the password, so removing PBKDF2 would
not migrate existing accounts — it would lock them out. Django re-hashes an
account on its next successful login, the single moment it holds the plaintext.
No data migration is involved, and none is possible.

Parameters are argon2-cffi's defaults, which Django tracks. Not tuned: a memory
or time cost picked without measuring this server is not better than the
maintained default, and a wrong one is worse.

This closes the gap found in P3b, where `docs/SECURITY.md` claimed Argon2 while
the project used PBKDF2. That was recorded honestly at the time rather than
quietly fixed; the claim is now true.

### 3. Status documentation

The header said "not committed", which stopped being true the moment P3 was
pushed. It now names the commit and lists what is actually outstanding, and the
readiness table gained row 10 for the unproven transactional email path, so the
two agree instead of contradicting each other.

### Validation

| Check | Result |
|-------|--------|
| Backend suite | **506 passed** (488 + 18 new) |
| `makemigrations --check` | No changes detected |
| `manage.py check` | No issues |
| `check --deploy` **with** a CI key | Passes (1 pre-existing `W019`) |
| `check --deploy` **without** the key | Still fails `domains.E001` — fail-closed intact |
| `docker compose config` with a CI key | Valid |
| `docker compose config` without it | Still refuses — fail-closed intact |
| Compose compliance assertions | Pass — `internal: true`, datastores internal-only, loopback ports |
| Frontend lint | 34 problems (17 errors, 17 warnings) — unchanged baseline |
| Frontend production build | Succeeds, 28 routes |
| `ci.yml` | Parses; both affected jobs generate a key before the steps that need it |

New tests: `tests/test_password_hashing.py` (18).

### Still outstanding for P3

Only one thing, and it is not a code problem: **no transactional email has ever
been delivered through a real provider.** Everything else on P3 is implemented,
tested and — after this commit — green in CI.


---

## P3 production preparation (staged, NOT deployed)

**Date:** 2026-09-11 **Status:** prepared on MateServer; **P3 is not deployed.**

Completed ahead of the P4 deployment and to be left in place:

| Item | State |
|------|-------|
| `/opt/MateMail/docker-compose.yml` | Replaced with the repository version at `aad6d50`, verified byte-exact (`sha256 8ec3ec81…`, 11604 bytes, LF) |
| `DKIM_ENCRYPTION_KEY` | Persistent production Fernet key generated on the server, stored in `.env` (mode 600), validated by an encrypt/decrypt round trip. Never printed, never committed |
| `DEFAULT_FROM_EMAIL` | `MateMail <noreply@mail.matemail.online>` |
| Backups | `docker-compose.yml.bak.20260911T081007Z`, `.env.bak.20260911T081007Z` |

A note for whoever deploys: this repository is checked out with
`core.autocrlf=true`, so the working copy of `deploy/docker-compose.yml` is
CRLF (11857 bytes) while the committed file is LF (11604 bytes). Take the file
from `git show <sha>:deploy/docker-compose.yml`, not from the working tree, or
the server receives something that is not the repository version.

**Deliberately NOT configured**, per DEC-013:

- `EMAIL_HOST` remains loopback; `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD`
  remain empty. No external SMTP credentials exist anywhere. The application
  detects this and refuses to send rather than reporting a false success, so
  the state is safe and visible rather than silently broken.
- `MAIL_ENGINE_ADAPTER=stub`, until P4 explicitly replaces it.

Untouched: mail ports (none listening), UFW (22/80/443 only), MX, SPF, DKIM,
DMARC, PTR, HELO. Production still runs `07dfc5d`, healthy, and every other
MateServer application is unaffected.

### What P4 inherits

The first suitable P4 milestone must perform the real end-to-end transactional
delivery test from `MateMail <noreply@mail.matemail.online>`, verifying actual
inbox delivery plus SPF, DKIM, DMARC where applicable, PTR/HELO alignment, and
no underlying engine branding leakage. **SMTP acceptance alone does not count.**
Until that passes, blocker 10 stays open.

---

## P4B — private Mail Engine installation (2026-09-11)

The real engine is installed on MateServer and validated. **It is not activated**:
production MateMail still runs `MAIL_ENGINE_ADAPTER=stub` on its previous image.

| | |
|---|---|
| Release | mailcow `2026-07b`, commit `02552ffefdf0869f988edf4a7e03822e8b467b34` |
| Containers | 18/18 running, none unhealthy; ClamAV, SOGo and FTS all enabled |
| Public mail exposure | **zero** — verified by scanning the host from off-server |
| TLS | Let's Encrypt for `mx.matemail.online`; all 8 services present it, hostname verification passes |
| Certificate renewal | host Certbot deploy hook, exercised for real, restarts only postfix/dovecot/nginx |
| DKIM private-key exposure | disabled; effective `$SHOW_DKIM_PRIV_KEYS = false` verified inside the running php-fpm container |
| API validation | 27/28 CRUD checks over verified TLS from a disposable container |
| Firewall delta | 63 rules added, 0 removed; all protective or standard Docker; none touch another application's network. UFW and `DOCKER-USER` unchanged; FORWARD policy still DROP |
| Other applications | all 30 non-engine containers healthy; public sites unaffected |

Full rollback runbook: `/root/matemail-p4b-ROLLBACK.md` on MateServer.

---

## P4C-A — engine contract remediation (2026-09-11)

P4B's validation against a real engine falsified four adapter assumptions. All
four looked correct in review and would have failed only in production.

| # | Defect | Effect if shipped | Status |
|---|---|---|---|
| 1 | `rotate_dkim_key` called `add/dkim`, which the engine refuses while a key exists | DKIM rotation could never succeed | Fixed — `delete/dkim` → `add/dkim`, retry-safe |
| 2 | `ensure_domain` omitted `dkim_selector`/`key_size`, which the engine reads at domain creation | Engine picks selector `dkim`; MateMail publishes `mm1` | Fixed — both fields sent on `add/domain` |
| 3 | A missing selector in the engine's reply was replaced with a guessed `"mm1"` | DNS published for a key nobody signs with; every message fails DKIM silently | Fixed — fails closed, with selector validation |
| 4 | Domain deletion left the DKIM key in the engine | **A new tenant inherits the previous tenant's private signing key** | Fixed — deprovisioning deletes the key first, unconditionally |

Defect 4 is the significant one and is written up in `SECURITY.md`.

**Why the test suite did not catch these.** `tests/fake_engine.py` had been
written to match the adapter's assumptions rather than the engine's behaviour:
it let `add/dkim` overwrite freely and tidily removed DKIM keys on
`delete/domain`. A test double that agrees with the bug cannot find it. It now
models all four real behaviours, with comments explaining why.

### Also in P4C-A

- **Production subnets pinned** (`matemail_internal` 172.23.0.0/16,
  `matemail_app` 172.24.0.0/16) — the engine binds to the app network's gateway,
  so an unpinned subnet made a routine network recreation a mail outage.
- **`extra_hosts: mx.matemail.online:172.24.0.1`** on `backend` and
  `celery-worker` only, so the engine is reached by hostname and TLS verifies.
- **Deploy checks** `mail_engine.E001/E002/E003/W001` — a real-engine
  configuration missing its URL or key, or using plain HTTP, now fails
  `check --deploy`. The stub is unaffected, so CI is untouched.
- **A swallowed exception removed** in the domain-delete view: a failure to queue
  engine cleanup was silently discarded, and that cleanup is what removes the
  signing key.
- Platform transactional mail configuration prepared for the engine (DEC-013),
  including correcting guidance that still said to use an external provider.

### Open, and blocking P4C-B

Item 1 of the original P4C-A list — cross-application reachability of the engine
— was **closed in P4C-A2** and is no longer a blocker. What remains:

| # | Item | Required before |
|---|---|---|
| 1 | A dedicated platform service mailbox and credential in the engine | the transactional delivery test |
| 2 | `MAIL_ENGINE_ADAPTER=mailcow` plus URL and key in production `.env` | real-adapter activation |
| 3 | PTR `169.58.114.252 -> mx.matemail.online` (currently `vmi3482362.contaboserver.net`) | real customer mail / private beta |
| 4 | 16 GB RAM upgrade | real customer mail / private beta |
| 5 | Full DNS authentication work: MX, SPF, DKIM publication, DMARC | real customer mail / private beta |
| 6 | Customer-facing mail ports and the abuse/policy gates that must precede them | real customer mail / private beta |
| — | Microsoft does not answer on TCP/25 | **not a blocker.** Outbound 25 works to Google, Yandex and Apple, so there is no general provider block. The cause of Microsoft's timeout is **not known** and must not be recorded as though it were; it is a deliverability investigation item before broad customer mail. |

---

## P4C-A2 — private engine boundary (2026-09-11)

Two things P4C-A left open.

### 1. The engine boundary was not actually isolated

The engine's ports were published on MateMail's own bridge gateway
(`172.24.0.1`). Measured: **every other Docker network on the host could reach
them** — six unrelated application networks plus the default bridge. A published
bind address selects a destination address, never a permitted source.

Replaced with a dedicated internal network and one TCP passthrough gateway:

```
backend / celery-worker ─▶ matemail_engine_link ─▶ HAProxy 3.2 LTS ─▶ nginx-mailcow:8453
   (and nothing else)      external, internal      TCP only, no TLS    postfix-mailcow:587
                           alias mx.matemail.online                 termination
```

After the change the control passes and **every** other network fails, including
`matemail_app` and `matemail_internal`. The old socket no longer exists. TLS
hostname verification succeeds on both protocols against the engine's real
certificate — the gateway passes it through untouched.

Consequences: the `extra_hosts` mapping and both pinned subnets are **removed**
(they existed only to serve the rejected design), and the engine's API ACL is
now scoped to the gateway's pinned identity rather than a NAT gateway address.

### 2. Domain deletion could lose key custody

Deleting a domain logged a failure to queue engine cleanup and then deleted the
local row anyway. Since that task is what removes the DKIM signing key, and the
local row is the only durable record that cleanup is owed, a broker failure at
that moment left an orphaned private key with nothing to reconcile against.

The local row is now deleted **only after** the task is durably queued. If the
enqueue fails the deletion is refused: the row stays, the API returns a
customer-safe `503` saying nothing changed, and the failure is logged as a
security event. The task is enqueued unconditionally, because
`mail_engine_provisioned` is cleared at the start of a re-provision and so can
read `False` while the engine still holds a key.

---

## P4C-A3 — versioned engine infrastructure + exact-revision Compose (2026-09-11)

Two reproducibility gaps closed before commit.

### 1. The engine gateway existed only on the server

The private link to the Mail Engine was configured entirely on MateServer.
Losing the host meant reconstructing MateMail's only path to its own mail engine
from prose.

`deploy/engine/` now versions the exact, non-secret configuration — five files,
each verified byte-identical to production by SHA256, each scanned for
credentials, plus a README covering installation, validation, rollback and
upgrade. Nothing secret-bearing is included: `mailcow.conf`, the API key, the
admin credential and all certificate material stay on the server.

The gateway holds a **static address on the engine network** (`10.244.0.247`)
because the engine's API ACL is scoped to it. That address is not guaranteed
free forever — the README documents checking it against every future engine
release before upgrading, and what to do if upstream claims it.

### 2. The deployment used whatever Compose file was on the server

`deploy/docker-compose.yml` now carries security-critical topology: which
services join the private engine link, that the link is external, that the
datastore network stays internal. Deploying new images against a stale Compose
file on the host lets those drift apart silently — the stack comes up, and the
boundary is not what the release says it is.

Compose and images are now one unit. The deployment:

1. verifies the requested SHA exists **and is an ancestor of the default
   branch**, so a dangling or abandoned commit cannot be deployed;
2. extracts `deploy/docker-compose.yml` from **that commit** via
   `git show <SHA>:...` on the Linux runner — not from the workflow checkout,
   because deploying an older published SHA must get that SHA's Compose file,
   and not from a Windows working tree, where line endings would be rewritten;
3. checks the engine-link network exists and is internal **before transferring
   anything**;
4. stages the candidate under `/opt/MateMail/.deploy/`;
5. validates it against the production `.env`, then asserts security invariants
   against Compose's own resolved JSON — port bindings, no host networking, no
   `:latest`, the internal datastore network, the external engine link, and that
   only `backend` and `celery-worker` join it;
6. backs up the live Compose file and `.env` (timestamped, bounded retention);
7. installs the candidate **atomically** via rename, then pulls and starts;
8. on any failure past that point, restores **both** Compose and `.env` and
   brings the previous stack back — never destructively.

The deploy still refuses to create `matemail_engine_link`. A network made with
Docker's defaults is routable rather than internal, which would silently reopen
the cross-application exposure the dedicated link exists to close.

---

## P4C-B in progress — activation, and the quota contract fix (2026-09-11)

### Done and live

- **`f787f039` deployed through the exact-revision workflow** (run 34645106354).
  The installed `/opt/MateMail/docker-compose.yml` is byte-identical to that
  commit's blob. Its first attempt failed safely in preflight on a first-run
  pruning bug, fixed in `aa549b46`; nothing had been transferred or changed.
- **The real Mail Engine adapter is active in production.**
  `MAIL_ENGINE_ADAPTER=mailcow`, `MailcowAdapter` constructed by both backend
  and worker, `check_health()` reachable.
- Private path verified from the live backend: `mx.matemail.online` resolves
  only to the private gateway, and both HTTPS 8453 and SMTP 587 STARTTLS
  validate the real certificate. Isolation re-tested — every unrelated Docker
  network still blocked.

### The defect activation found

MateMail **could not create a domain at all**. The adapter sent a hard-coded
domain total of `0` with a positive per-mailbox ceiling; the engine reads that
as a contradiction and refused every domain
(`mailbox_quota_exceeds_domain_quota`), after which every mailbox call failed
with `access_denied` because the domain did not exist.

Fixed per **DEC-016**: storage is now three explicit `Plan` fields, carried on
`DomainSpec` and sent verbatim, with the relationship validated by MateMail
before the engine is called. The total is stored, never derived, so shared-pool
plans remain expressible.

### Current Private Beta policy (temporary — not public pricing)

```
Price        : free
Provisioning : admin approval required
Mailbox size : 1 GB default, 1 GB maximum
```

Applied to the free `trial` plan. The commercial tiers keep their own ceilings.
**Admin approval is policy only — no enforcement mechanism exists yet**; the
missing point is workspace creation at signup, and it belongs with P5.

### Still open in P4C-B

The quota fix is **not yet deployed**. Remaining, in order: commit and push →
CI → redeploy → re-run the disposable lifecycle test → platform sender →
DNS/PTR external-action gate (**PTR is still `vmi3482362.contaboserver.net`**)
→ first real delivery and recipient confirmation.

No domain, mailbox, platform sender or DNS record has been created. No mail has
been sent. No public mail port is open. UFW unchanged.

---

## P4 COMPLETE — first real delivery verified (2026-09-12)

MateMail sent a real email, through its own infrastructure, and it arrived.

### The delivery

One message, sent from production via `apps.accounts.mailer.send_transactional()`
— the same function behind every verification link, password reset and team
invitation. Not `swaks`, not `sendmail`, not the engine's UI.

```
send_transactional()  →  django.core.mail  →  mx.matemail.online:587
                      →  private gateway   →  postfix-mailcow
                      →  rspamd DKIM sign  →  outbound TCP/25
                      →  Gmail             →  Primary Inbox
```

| Evidence | Result |
|---|---|
| Application result | `send_transactional()` returned `True` |
| Queue ID / Message-ID | `AD5EE13BE39` / `<178920022415.130...@00a3f41e29d7>` |
| Submission | TLS 1.3, `AUTH PLAIN` as `noreply@mail.matemail.online` |
| Outbound | **Verified** TLS 1.3 to `gmail-smtp-in.l.google.com` |
| Gmail response | `250 2.0.0 OK` |
| **SPF** | **pass** — `client-ip=169.58.114.252` |
| **DKIM** | **pass** — `header.i=@mail.matemail.online`, `header.s=mm1` |
| **DMARC** | **pass** — `header.from=mail.matemail.online` |
| **PTR / HELO / A** | aligned, all `mx.matemail.online` ↔ `169.58.114.252` |
| **Placement** | **Gmail Primary Inbox** — not Spam |
| Queue after send | empty; no defer, no bounce, no retry |

### What this does and does not prove

**Proven:** the engine works, the adapter works, the private network path works,
the platform sender works, and the infrastructure can deliver an authenticated
email that a major provider accepts and files in the Inbox.

**Not proven:** future deliverability or inbox placement. One message to one
provider from a new sending IP says authentication and routing are correct. It
says nothing about reputation under volume, behaviour at other providers, or
what happens when real customer mail starts. Warm-up and monitoring remain
ahead, and **Microsoft's TCP/25 timeout is still unexplained**.

### Platform sender

| | |
|---|---|
| Sender | `MateMail <noreply@mail.matemail.online>` |
| Platform domain | `mail.matemail.online` |
| DKIM selector | `mm1`, 2048-bit, engine-held |
| Rate limit | 60 messages/hour |
| SMTP | `mx.matemail.online:587`, STARTTLS, authenticated |
| Credential | server-only, root-readable, never committed or printed |

DMARC stays at `p=none`. Tightening belongs after reporting and broader
validation exist — a stricter policy with nowhere to receive failure reports
breaks mail invisibly.

### Non-blocking cleanup: Message-ID right-hand side

The delivered message carried `<...@00a3f41e29d7>` — the Docker container
hostname — because Django generates the Message-ID before submission using the
local hostname. It should be `<unique-id@mail.matemail.online>`.

Not a blocker: Gmail accepted the message, all three authentication checks
passed, and it reached the Inbox. rspamd noted it only as `MID_RHS_NOT_FQDN`
(+0.50 against a 15.0 threshold, on a message scoring −19.50).

It is still worth fixing — a container ID in a public header is a small
infrastructure leak and a hygiene issue some filters weigh. The fix is a
`Message-ID` header set from `DEFAULT_FROM_EMAIL`'s domain in
`send_transactional()`. Deliberately **not** done in this closeout: it changes
outbound mail behaviour and belongs with tests, not bundled into a
documentation pass.

### Still required before customer mail

P4 completing does **not** make MateMail ready for customers. Remaining:
**P5** mail policy enforcement · **P7** operations and monitoring ·
**P7.5** free accounts (DEC-015) · then Private Beta. P6 backup and restore
is complete, with an offsite repository still to be configured.

No public mail port is open. No customer domain or mailbox exists.


---

## P6 COMPLETE — backups taken, and restored (2026-09-19)

Backups exist, run on a timer, and have been restored. The restore is the part
that counts: a backup nobody has restored is a guess about the worst day of the
deployment's life.

### What was built

`deploy/backup/` in the repository, installed to `/opt/MateMailBackup/` by
`install.sh`. Engine is restic 0.18.1 — encrypted, deduplicating, snapshot
based. One snapshot per run, daily at 01:30 UTC with `Persistent=true`,
retention 14 daily / 8 weekly / 6 monthly. Rationale and the full exclusion
list are in DEC-032; the operator procedures are in `docs/BACKUP_RESTORE.md`.

### What was proven on MateServer

Proof used synthetic data, because production has no customer mailboxes and an
empty system proves nothing about restoring mail. A `p6-drill.invalid` domain —
`.invalid` is reserved and never resolves, so nothing could leave the host —
carried three mailboxes and ten real messages delivered through Postfix,
Rspamd and Dovecot. All of it was removed afterwards; production ended at zero
domains, zero mailboxes, zero retired storage, zero DKIM keys.

```
full restore drill      PASS — 13 staged files verified against their manifest
                        checksums; both databases loaded into a disposable
                        PostgreSQL on --network none; restored row counts
                        matched the manifest exactly (1 domain, 3 mailboxes,
                        1 retired storage, schema v4); 2 DKIM keys parsed as
                        usable keys; 40 mail files restored
mailbox restore, live   PASS — default restored beside the mailbox, live
                        mailbox untouched at 5 messages, copy owned 5000:5000
mailbox restore, guard  PASS — --in-place against an active mailbox REFUSED
mailbox restore, force  PASS — snapshot's 3 messages restored, all 5 live
                        messages (including 2 that arrived after the snapshot)
                        preserved in .replaced-<ts>, indexes rebuilt
deleted mailbox         PASS — Maildir destroyed, then restored by address
                        alone via retired_mailbox_storage; 2 messages back,
                        content verified by marker, owner 5000:5000
```

### Failure tests

```
wrong repository password       refused (exit 12)
database unreachable            backup failed, no snapshot written
DKIM volume missing             backup failed, no volume created
corrupted pack file             restic check --read-data detected it
undecryptable repository        drill failed rather than reporting success
concurrent run                  second run declined the lock and exited
```

### Four defects found by the drills, not by the code

Each of these produced a backup or a drill that *looked* successful.

**The manifest recorded nothing.** Row counts were built with
`count(*)||":"||count(*)`. In PostgreSQL `":"` is a quoted identifier, not a
string, so every query failed with `column ":" does not exist`, the error went
to `/dev/null`, and the fallback wrote `?` for every count. The drill's
strongest check — do the restored rows match what was captured — had nothing to
compare against and said so, which is the only reason it was caught. Counting
now uses psql's own field separator, and failing to count is fatal.

**The drill passed without verifying.** It logged "manifest recorded no
expectation" and continued to PASS. It now fails: a drill that passes without
comparing anything is precisely the failure mode P6 exists to remove.

**A missing DKIM volume produced a successful backup with no keys.**
`docker run -v name:/path` *creates* a named volume that does not exist rather
than failing. A renamed or lost DKIM volume would have been backed up as an
empty directory, reported as success, every day, until someone needed it.
Existence is now asserted before anything may mount it, and the key count is
recorded in the manifest and checked by the drill.

**The drill passed by winning a race.** The postgres image runs a temporary
server during initdb; `pg_isready` on the unix socket answers during that phase
and the server then restarts. The first drill passed; the next failed at
`createdb` with "No such file or directory". Readiness is now probed over TCP,
which the temporary server does not listen on, and requires two consecutive
successes.

All four have regression tests in `backend/tests/test_backup_restore.py`.

### The honest limitation

**There is no offsite repository.** `/opt/MateMailBackup/repo` is on the
production host. It survives deletion, corruption and operator error. It does
not survive losing the machine. That is retention, not disaster recovery, and
calling it anything else would be the kind of claim this project does not make.
`OFFSITE_REPOSITORY` is the one setting that closes it; no provider is assumed.
Until it is set, every run logs the gap and every manifest records
`"offsite": false`.

### Also found

`backend/apps/backups/` exposes `/api/backups/` to tenant admins and backs
nothing up — it counts rows, invents a size, and marks the job completed.
Recorded in `docs/TODO.md`. It has no relationship to the platform backups
above and must not ship to customers as a backup feature.

---

## P7 — monitoring and observability (local implementation, 2026-09-19)

Implemented locally and validated; not yet deployed. The runtime half —
deployment, failure injection, alert-state verification and resource
measurement — follows the commit.

### Measured baseline that decided the architecture

```
6 cores · 11 GiB RAM · 3.3 GiB available · 1.3 GiB swap ALREADY in use
72 containers using ~6.6 GiB · 42 GB of 193 GB disk · 5% inodes
```

That is a host under memory pressure before anything is added. The
conventional Prometheus stack — postgres_exporter twice, redis_exporter twice,
blackbox_exporter, cAdvisor — would have meant six more containers, and
cAdvisor would have wanted the Docker socket. So: four containers plus one
root-owned host collector, capped at 792 MB in total and with no container
holding any privilege (DEC-034).

### What was built

```
deploy/monitoring/
  docker-compose.yml          4 services, all 127.0.0.1, all memory-limited
  collectors/                 the root host collector, 18 sections
  prometheus/                 config, 60 alert rules, promtool unit tests
  alertmanager/               grouping, inhibitions, no SMTP anywhere
  grafana/                    provisioned datasource + 3 dashboards
  systemd/                    collector service and 60s timer
  logrotate/                  bounds Docker's previously unbounded logs
  install.sh
docs/MONITORING.md            operator runbook
backend/tests/test_monitoring.py
```

Grafana is on **3040**, not 3000, because `127.0.0.1:3000` is already taken by
another application on this shared host.

### What it answers

All ten Native services individually and as an all-or-nothing signal; MateMail
through its own health endpoint rather than container status; Postfix queue by
state with oldest-message age; quarantine; delivery, bounce, reject, 4xx, 5xx
and TLS-failure counters; Dovecot authentication as aggregate counts; Rspamd,
ClamAV with signature freshness, Olefy; Unbound including the NE1 DNSSEC
property; both PostgreSQL instances; both Redis instances; per-volume storage
growth; backup timer, last result and snapshot age; certificate expiry;
MX/A/PTR/SPF/DKIM/DMARC; public port and firewall posture; Celery; Mailcow and
the P5 bridge as transitional signals; and an NE6 readiness composite.

### Three decisions worth stating

**A failing collector section publishes nothing** (DEC-035). It never
substitutes a default. A collector that cannot reach Postfix and therefore
reports `queue_total 0` has not reported a healthy queue, it has reported a
fiction that looks like the normal value. Metrics go stale instead, and
staleness is alertable.

**Metric labels come from a bounded allowlist** (DEC-036), enforced by a test
that reads the collector's syntax tree. The collector reads Dovecot logs, which
are full of mailbox addresses. A label built from one would publish customer
data *and* create one series per address.

**Alerts are never delivered through the mail system under test** (DEC-037).
Alertmanager has no SMTP configuration at all.

### Validation

```
promtool check config                    PASS
promtool check rules                     PASS
promtool test rules                      PASS — 15 rule unit tests
backend/tests/test_monitoring.py         50 passed
backend/tests/test_backup_restore.py     60 passed (unchanged)
docker compose config                    PASS
git diff --check                         clean
secret scan                              no key material, no committed .env
```

Seven mutations were introduced to prove the tests catch real defects, and all
seven were caught: Grafana bound to `0.0.0.0`; the Docker socket mounted into
Prometheus; a memory limit removed; a mailbox address added as a metric label;
a capture group added to a log pattern; the exposure alert given a 10-minute
delay; and the backup-staleness threshold broken so that a *fresh* backup would
alert — that last one failing the promtool unit tests, which proves they check
real threshold semantics rather than merely parsing.

### Defect found and fixed during P7

**Docker had no log rotation whatsoever.** There is no `/etc/docker/daemon.json`
on MateServer, so the default `json-file` driver had been running unbounded
since the host was built; individual container logs had reached 45 MB. The
usual fix is `log-opts` in `daemon.json`, but that applies only to newly created
containers and needs a Docker daemon restart, which here would disturb 72
containers including Mailcow and several unrelated production applications.
`logrotate` with `copytruncate` bounds the same files today and disturbs
nothing.

### Known warnings, both pre-beta rather than NE6 blockers

```
OFFSITE_BACKUP_CONFIGURED = NO   BackupOffsiteNotConfigured fires permanently,
                                 by design, and re-notifies weekly
ALERT_RECEIVER_CONFIGURED = NO   alerts fire and are visible over the tunnel;
                                 nothing is pushed off the host
```

Both are deliberately excluded from `matemail_ne6_ready`. Including them would
either block NE6 on an unrelated purchasing decision or tempt someone to mark
them green. NE6 technical readiness is not Private Beta readiness.

---

## P7 COMPLETE — monitoring deployed and alerts proven (2026-09-19)

Release `5a611fb3f6455d568faf08e57e016b5e429f38e2`, CI green. No application
image was rebuilt: no application source changed, and monitoring configuration
does not need one.

### Deployed

`/opt/MateMailMonitoring/` — Prometheus v3.1.0, Alertmanager v0.28.0,
Grafana 11.5.1, node-exporter v1.8.2, all bound to `127.0.0.1`, plus the root
collector on a 60-second systemd timer. 3 Prometheus targets up, 63 alert rules
loaded and healthy, 18 of 18 collector sections reporting OK, 3 dashboards
provisioned from repository files and querying live data.

### Alerts validated against real induced failures

Not inspected — induced, one at a time, each restored before the next.

```
Native Redis stopped   rate_limit_enforcement_available 1 -> 0
                       native_services_healthy 10 -> 9, all_healthy 1 -> 0
                       4 alerts PENDING, then after the 3m window
                       NativeServiceDown and RedisDown FIRING and delivered
                       to Alertmanager; the two 5m rules correctly still
                       pending. On restore every metric returned and every
                       alert RESOLVED.
Native API stopped     native_api_up 1 -> 0; NativeApiDown + NativeServiceDown
                       pending; recovered
Unbound stopped        dns metrics -> 0; UnboundDown + DnsResolutionFailing
                       pending; recovered
Olefy stopped          olefy_up 1 -> 0; OlefyDown pending; recovered
ClamAV stopped         clamav_up 1 -> 0; ClamAVDown + NativeServiceDown
                       pending; recovered
Backup timer disabled  backup_timer_enabled 1 -> 0; BackupTimerDisabled
                       pending; re-enabled and recovered
Collector section fail backups section_ok -> 0 and that section published
                       NOTHING, while the other 17 sections were unaffected
```

The full metric → rule → pending → firing → Alertmanager → resolve path is
therefore proven end to end, not assumed.

Every observed value was checked against a baseline gathered independently
before deployment: Native 10/10, schema v4, queues empty, certificate 81 days,
snapshot age 3.8 h matching the recorded snapshot time, 2 snapshots,
offsite_configured 0, MX/A/PTR/SPF/DKIM/DMARC all correct, zero public mail
ports, UFW active, `matemail_ne6_ready` 1 with 0 checks failed.

### Three defects found during deployment, all fixed in-phase

**Configuration was unreadable by the containers that use it.** `install.sh`
runs `umask 077` so the runtime `.env` is private; that same umask made every
copied file root-only. Prometheus and Alertmanager run as uid 65534 *by
design*, could not read their own configuration, and crash-looped with
"permission denied" while the compose file and the volumes both looked
perfectly correct. Permissions are now set explicitly — 755/644 for
configuration, which is committed to the repository and not secret, with the
`.env` locked to 0600 separately.

**Bind mounts pointed at deleted directories.** `install.sh` replaces the
config directories with `rm -rf` plus a copy, creating new inodes. A running
container's bind mount was resolved at start and still referred to the removed
directory, so Grafana read provisioning that no longer existed — files correct,
permissions correct, directory unreadable inside the container. Fixed with
`--force-recreate`, which also closes the better-known trap that Compose never
compares the *contents* of a mounted file, the one that cost NE3 five days with
Rspamd.

**A partially-failing collector section published partial data.** The section
wrapper caught the exception but did not roll back what had already been
emitted, so the backup section published the systemd timer state, then failed
reading the repository configuration, and left behind a half-picture that read
as a complete one. That is precisely the failure DEC-035 exists to prevent, and
the original test only read the source rather than exercising the behaviour.
`section()` now snapshots and truncates on failure, and three behavioural tests
drive a section through a failure and assert nothing survives. Reverting the
fix fails those tests.

### Resource usage, measured after settling

```
Prometheus     41 MiB / 400 MiB limit
Grafana        72 MiB / 200 MiB limit
Alertmanager   18 MiB /  96 MiB limit
node-exporter   9 MiB /  96 MiB limit
collector      55 MiB peak, transient, once per minute
                            ~140 MiB resident in total
disk           27 MB volumes + 348 KB config; images ~1.3 GB
host           swap FELL from 1313 MB to 1054 MB; disk 42G -> 43G
```

Comfortably inside the budget. No correction was needed.

### Final state

Native 10/10 healthy · MateMail 6/6 healthy · Mailcow 20 running · P5 bridge
healthy · monitoring 4/4 healthy · `MAIL_ENGINE_ADAPTER=native` · Native and
Mailcow queues empty · quarantine empty · restic check clean, 2 snapshots ·
UFW checksum byte-identical to the pre-P7 baseline · public listeners exactly
22, 80, 443, 4000 · zero public mail ports · zero public monitoring ports ·
zero published Native ports · DNS, PTR and certificate unchanged · no Internet
mail sent by Native · no alerts firing.

### Known warnings — pre-beta, not NE6 blockers

```
OFFSITE_BACKUP_CONFIGURED = NO    local repository is retention, not disaster
                                  recovery; BackupOffsiteNotConfigured is
                                  designed to fire permanently
ALERT_RECEIVER_CONFIGURED = NO    alerts fire, group and are visible in
                                  Alertmanager over the SSH tunnel; nothing is
                                  pushed off the host
```

Both are deliberately excluded from `matemail_ne6_ready`. NE6 technical
readiness is not Private Beta readiness.

---

## NE6 COMPLETE — platform outbound now leaves through the Native Engine (2026-09-19)

Release `46b67723ff985053568d907e8a104aebb15f48e2`, CI green. No application
image was rebuilt: no application source changed.

```
Before NE6:   MateMail platform outbound  ->  Mailcow  ->  Internet
After  NE6:   MateMail platform outbound  ->  Native Engine  ->  Internet
```

### The switch was one network alias

`mx.matemail.online` is a Docker network alias on `matemail_engine_link`. NE6
moved it from the Mailcow-owned gateway to a new Native-owned one. `EMAIL_HOST`,
`EMAIL_PORT`, `EMAIL_HOST_USER`, `DEFAULT_FROM_EMAIL` and the SMTP credential
are all **unchanged**, no container IP entered any settings file, and no DNS
record was touched. Rollback is moving the alias back, which was rehearsed.

### Real Internet delivery, through the real application

Sent with `apps.accounts.mailer.send_transactional()` — the function behind
every verification link and password reset — to an operator-controlled Gmail
account, the same one used for the P4 delivery test.

```
SPF    pass    client-ip=169.58.114.252
DKIM   pass    header.i=@mail.matemail.online  header.s=mm1
DMARC  pass    header.from=mail.matemail.online   (adkim=s, aspf=s)
TLS    1.3     TLS_AES_256_GCM_SHA384 to the recipient MX
MX     250 2.0.0 OK        Placement: Primary Inbox
Mailcow involvement: NONE  (zero lines in its log)
```

DKIM was also verified **cryptographically before sending**, with dkimpy
against the public DNS key — signature and body hash valid — rather than
trusting the presence of a header.

### DKIM was adopted, so no DNS changed

The existing `mm1` private key was migrated out of Mailcow by a one-time script
that reads the key on stdin, refuses anything whose public half does not match
DNS, and was deleted afterwards. Three independently derived public-key
fingerprints — Mailcow's stored key, Native's key on disk, and the `p=` tag in
public DNS — were required to be identical before the transport switch, and
were:

```
12232cbc872dd3ef533293da2433bf2111eed96b05abd27c374cb34544d088b7
```

This mattered more than expected: creating the domain in Native
**auto-generated a fresh mm1 key** that did not match DNS. The fingerprint gate
caught it and the real key replaced it.

### Four defects found and fixed in phase

Each one produced a system that looked like it was working.

**Native Postfix had no certificate at all** while its submission service
required STARTTLS. It would have refused every message.

**Native Postfix logged nothing.** Postfix defaults to syslog and the container
has none, so it delivered mail and left no record — and P7's delivery,
deferral, bounce and rejection counters had been parsing an empty log and
reporting a confident zero since the day they were written.

**DKIM signed nothing.** Rspamd's `use_esld` default rewrote
`mail.matemail.online` to `matemail.online`, missed the selector map, and
delivered mail UNSIGNED with no error. NE3's signing proof had used a
two-level domain where that collapse is invisible. Every MateMail sending
domain is a subdomain, so customers would have hit this too.

**Health checks poisoned a monitoring signal.** HAProxy's TCP check and the
container's own probe each dropped half-open sessions on Postfix ~4 times a
minute, all counted as `connection_lost` — the metric meant to reveal clients
failing mid-session. A real failure would have been invisible in the noise.

All four have regression tests.

### Failure behaviour, injected not assumed

```
submission gateway down   send_transactional() False; ZERO Mailcow lines
rspamd down               451 4.7.1 at CONNECT, deferred
clamav down               451 4.7.1 antivirus unavailable; never unscanned
native redis down         450 4.7.1 rate limiting unavailable; fails CLOSED
unbound down (external)   refused 4xx, nothing accepted, nothing queued
```

There is **no automatic fallback to Mailcow** and that was proven, not
asserted (DEC-040).

### Final state

Native 11/11 healthy (the gateway is the eleventh service, monitored like the
rest) · MateMail 6/6 · Mailcow 20 running with its platform domain, mailbox,
DKIM key and credential intact · P5 bridge healthy · monitoring 4/4 · backups
enabled · queues and quarantine empty · UFW checksum byte-identical to the
pre-NE6 baseline · public listeners exactly 22, 80, 443, 4000 · zero public
mail ports · zero published Native ports · MX, A, PTR, SPF, DKIM and DMARC
unchanged · `matemail_ne6_ready` = 1 · only the known
`BackupOffsiteNotConfigured` warning firing.

### What NE6 did not do

No public port opened. No Internet inbound. No customer submission or IMAP. No
mailbox migration. Mailcow still carries customer/Internet mail and is removed
at NE8. Broad deliverability testing across Gmail, Microsoft and Zoho is NE7's.

---

## NE7 COMPLETE — the Native Engine is a public Internet mail server (2026-09-20)

Release `f50c673690c0f23748cf89d593321d3775a986f1`, CI green. No application
image rebuilt: no application source changed.

```
Internet  ->  :25   ->  Postfix -> scanners -> Dovecot LMTP -> Maildir
client    ->  :587  ->  STARTTLS + AUTH -> Postfix -> Internet MX
client    ->  :993  ->  Dovecot -> Maildir
MateMail  ->  private gateway (NE6) -> Postfix -> Internet MX
```

Mailcow appears in none of them, and keeps its platform domain, mailbox, DKIM
key, credential and gateway as the rollback.

### Proven with real mail, both directions

**Gmail → MateMail.** `mail-pz2-f12.google.com[74.125.228.12]` connected to the
public MX and chose STARTTLS (TLS 1.3). Queue `372E5105F27`. Rspamd scored it
−2.00/15.00 with `R_SPF_ALLOW`, `R_DKIM_ALLOW`, `DMARC_POLICY_ALLOW` and
`ARC_ALLOW` — Native validated Gmail's own authentication. LMTP delivered it
into the exact `storage_id` provisioned for the mailbox, and it was read back
over public IMAPS from a Windows workstation with correct headers, body, stable
UIDs and working `\Seen` semantics.

**MateMail → Gmail, from an external client over public 587.** STARTTLS, AUTH
PLAIN, and at Gmail: `spf=pass`, `dkim=pass (s=mm1)`, `dmarc=pass`, Primary
Inbox. The `Received: ... with ESMTPSA` line from the workstation address is
what distinguishes this from NE6, which used the private gateway.

### Refused, verified from outside

```
open relay, plaintext and over TLS        554 5.7.1
unknown recipient                          550 5.1.1
inactive mailbox / inactive domain         550 5.1.1
submission without STARTTLS                530 5.7.0
wrong password, SMTP and IMAP              535 / AUTHENTICATIONFAILED
sender not owned by the authenticated login 553 5.7.1
EICAR attachment                           554, CLAM_VIRUS(2000.00)
```

Rspamd's RBL and SPF checks proved themselves incidentally: a forged
`gmail.com` sender from a residential IP scored 16.40 and was rejected.

### Four defects found, all silent, all fixed in phase

**Inbound mail hard-bounced during a Dovecot restart.** With
`lmtp:inet:dovecot:24`, a stopped container makes Docker's DNS answer NXDOMAIN,
which Postfix treats as *permanent*: `dsn=5.4.4, status=bounced`. A routine
restart returned customer mail to its senders. Now delivers to a pinned
address, which defers and recovers — verified by repeating the outage
(DEC-044).

**fail2ban would never have banned an IMAP brute-force.** The filter carried
Dovecot 2.3's wording and was validated against a hand-written sample of the
same wording, so it agreed with itself and matched zero real lines. Dovecot 2.4
says `Login aborted:`. It would have run, reported healthy and shown no bans.

**IMAP authentication was invisible to monitoring, for two independent
reasons** — the collector captured only stdout while Dovecot logs to stderr,
and the patterns used the same stale 2.3 wording. Either alone was enough.

**Sender-login restrictions were declared where Postfix ignores them.** Port 25
has SASL disabled by design, so both restrictions were silently skipped and
warned about on every inbound connection. Moved to submission, where they now
demonstrably fire.

All four have regression tests, and the fail2ban and collector tests now use
log lines captured from the running server rather than written from memory.

### Security posture

Public: 22, 25, 80, 443, 587, 993. Closed: 110, 143, 465, 995 — and 143 does
not listen at all. Every internal service verified unreachable from the
Internet.

UFW changed (three rules added), but **UFW is not what enforces this**: Docker
published ports bypass it, proven by removing the 993 rule and finding 993 still
reachable. The bind address is the control (DEC-041). That is also why
fail2ban bans into `DOCKER-USER`, verified by banning a TEST-NET address and
reading the resulting rule (DEC-043).

### Final state

Native 11/11 healthy · MateMail 6/6 · Mailcow 20 running · P5 healthy · P6
timer active · P7 4/4 with `matemail_ne6_ready = 1`, 0 checks failed, 0
collector sections failed · fail2ban and its log shipper active · queues and
quarantine empty · DNS, PTR, SPF, DKIM and DMARC unchanged · certificate valid
to 2026-12-10 · only `BackupOffsiteNotConfigured` firing.

### Not done, deliberately

No POP3, no port 465, no plaintext IMAP, no public signup, no free mailboxes,
no autoconfig, no Mailcow removal. Microsoft and Zoho protocol interop is
verified; real inbox delivery to them is not, because no operator-controlled
mailbox exists on either and NE7 did not invent one.

---

## P11 — MateMail PostBox (webmail)

**Built and tested locally. Not deployed, and therefore not proven.** No
message has been read or sent through PostBox on MateServer. Everything below
distinguishes the two.

### What exists

A complete webmail application in `backend/apps/postbox/` and
`frontend/app/postbox/`, served from `postbox.matemail.online` — a third
hostname on the same two containers, with no new port and no new service.

| Area | State |
|------|-------|
| Sign-in against Dovecot, session bound to one mailbox | Built |
| Folder list, standard folders created if absent | Built |
| Message list — paging, search, starred filter | Built |
| Reader — sanitised HTML, blocked remote images, inline `cid:` images | Built |
| Attachments — download, `Content-Disposition: attachment`, `nosniff` | Built |
| Compose, reply, reply-all, forward, drafts, scheduled send | Built |
| Flags and folder actions — read, star, archive, trash, restore, delete | Built |
| Identities (mailbox + aliases), signatures | Built |
| Contacts, scoped to the mailbox | Built |
| Rules compiled to Sieve, vacation responder, via ManageSieve | Built |
| Preferences — theme, density, reading pane, page size, timezone | Built |
| Own-password change, with other sessions revoked | Built |
| Thread view | **Not built** — messages are listed and read individually |

### What was removed

`WebmailSSOView`, `/api/webmail/`, the internal token validator, and the
Workspace's "Open webmail" button. The view minted a login token for any
mailbox in the caller's tenant; it was inert only because nothing could
complete the login, and PostBox would have completed it (DEC-049).

`WEBMAIL_BASE_URL`, which no code read — closing audit item L6.

`conversation_view`, a stored preference the API returned as `true` while
nothing anywhere grouped conversations. A preference that reports a capability
the product does not have is a false claim in the API surface, so the field is
gone rather than documented.

### Tests

| Suite | Result |
|-------|--------|
| `tests.test_postbox_security` + `tests.test_postbox_mail` | **73 tests, OK** |
| Full Django suite (`manage.py test`) | **1560 tests, OK**, 5 skipped |
| pytest-only regression files (the four CI names) | **217 passed**, 2 skipped |
| `manage.py check` | no issues |
| `manage.py check --deploy` (prod settings) | **no issues** |
| `makemigrations --check` | no changes detected |
| Frontend `tsc --noEmit` | clean |
| Frontend ESLint | 17 errors / 18 warnings; **0 in PostBox code**; baseline 18 errors |
| Frontend `next build` | compiled successfully; all four `/postbox` routes present |

Host routing was checked against the production-shaped standalone server, with
the same `NEXT_PUBLIC_*` build arguments and the same `HOSTNAME=0.0.0.0` the
image sets:

```
portal.matemail.online/login               -> 200
portal.matemail.online/app                 -> 200
portal.matemail.online/postbox             -> 308 https://postbox.matemail.online/
postbox.matemail.online/                   -> 200
postbox.matemail.online/login              -> 200
postbox.matemail.online/settings           -> 200
postbox.matemail.online/contacts           -> 200
platform.matemail.online/login             -> 200
platform.matemail.online/organizations     -> 200
app.matemail.online/                       -> 308 https://portal.matemail.online/
app.matemail.online/login                  -> 308 https://portal.matemail.online/login
app.matemail.online/app/domains            -> 308 https://portal.matemail.online/app/domains
```

The legacy host preserves the path. A redirect that dropped it would send
everyone to a login page instead of to their bookmark, and would still look
like it was working.

One harness note worth keeping. Running the standalone server with
`HOSTNAME=127.0.0.1` makes Next compute an origin that differs from the `Host`
header, so it treats the middleware rewrite as an *external* proxy and fails
with a TLS error. That is the harness, not the product — the image sets
`HOSTNAME=0.0.0.0`, which is what the run above used.

### Hostnames (DEC-055)

The customer console is the **MateMail Workspace** at `portal.matemail.online`.
`app.matemail.online` is a 308 redirect and serves nothing; nginx does it at the
edge and `frontend/middleware.ts` does it as well, so the application is correct
even where the vhost has not been installed. The old name stays on the
certificate and in `DJANGO_ALLOWED_HOSTS` for as long as the redirect exists.

**Not deployed.** `portal.matemail.online` has no DNS record, no certificate and
no installed vhost. Until that is done the live customer hostname is still
`app.matemail.online`, and the redirect above exists only in the repository.

### What is NOT proven

None of this has run against production. Specifically unproven:

- No real sign-in against production Dovecot. The Dovecot image carrying the
  master-identity entrypoint has not been built or deployed, so the credential
  behind PostBox does not yet exist on the server.
- No message read, sent, or received through PostBox.
- The IMAPS gateway frontend is written but has never carried traffic.
- `postbox.matemail.online` has no DNS record, no nginx vhost installed and no
  certificate.
- Sieve upload over ManageSieve has never reached a real Dovecot.

A green test suite says the code does what it was written to do. It says
nothing about whether Dovecot accepts the master identity on MateServer, and
that is the one thing this phase cannot claim until it is deployed.

### Deployment order

The order matters and is written out in `docs/DEPLOYMENT.md` § *PostBox
deployment (P11)*. In short: the master password into both `.env` files first,
then the Dovecot image, then the application, then DNS/nginx/TLS. The Dovecot
entrypoint refuses to start without the credential, by design.

---

## Mail Client Discovery & Outlook Compatibility

**Built and tested locally. Nothing deployed, no DNS changed.**

### What exists

An Outlook POX Autodiscover compatibility endpoint in
`backend/apps/autodiscover/`, mounted at the root (`/autodiscover/autodiscover.xml`
and four case variants, because Outlook does not agree with itself about case),
to be served from `autodiscover.matemail.online`.

| Behaviour | State |
|---|---|
| Returns IMAP `mx.matemail.online:993`, implicit TLS | Built |
| Returns SMTP `mx.matemail.online:587`, STARTTLS, auth required | Built |
| Login name is the full address | Built |
| No Exchange / MAPI / EWS / ActiveSync / POP block | Built |
| Domain eligibility = ownership VERIFIED + ACTIVE\|WARNING + provisioned | Built |
| Identical response for a real and an invented local part | Built |
| defusedxml parsing, 8 KiB body cap, rate limited per IP | Built |
| Optional `_autodiscover._tcp` SRV shown in the Workspace | Built |
| Mail client settings section in PostBox | Built |
| Native `user@organization.matemail.online` domains | **Not implemented — see below** |

### The SPF correction

Customer SPF is now `v=spf1 include:_spf.matemail.online ~all`. It was
`include:matemail.online`, which made the product's website domain double as
the provider's SPF authorisation record (DEC-056).

`SPF_INCLUDE_DOMAIN` is its own setting. `MAIL_DOMAIN` is untouched, so DMARC
reports still reach `dmarc@matemail.online` and MX still points at
`mx.matemail.online`.

**This is a customer-visible migration.** Every existing customer domain must
update its SPF record, and until it does the DNS health check will report SPF
as FAILED. Nothing in this phase notifies anyone — there is no migration email,
no banner and no grace period that accepts both forms. That is a deliberate
omission rather than an oversight: accepting both would mean the old
authorisation keeps working indefinitely, which is the thing being removed.
Sequencing the customer communication is outstanding work.

### Native organization domains

`user@organization.matemail.online` **does not exist in the codebase.** There
is no model field, no flag and no concept of a platform-owned domain anywhere
in `backend/apps/`. Autodiscover therefore treats every domain the same way:
through the ownership and provisioning gate that already exists.

Nothing was invented for it. When native domains are built, they will need an
explicit decision about eligibility — a MateMail-owned domain has no customer
DNS to prove ownership with, so `DomainOwnership.VERIFIED` cannot mean the same
thing, and that is a design question, not a code change.

### Autodiscover is outside the DNS health score

The score stays four records at 25 each: MX, SPF, DKIM, DMARC. The SRV record
is checked and stored with `is_scored=False` and shown in a separate **Mail
Client Discovery** section.

A domain with no SRV record scores 100 and is ACTIVE. Six tests in
`test_dns_spf_and_discovery.py` pin this from both directions — a missing SRV
cannot lower the score, and a present one cannot raise it or rescue a domain
with no MX.

### Tests

| Suite | Result |
|---|---|
| `tests.test_autodiscover` | **29 tests, OK** |
| `tests.test_dns_spf_and_discovery` | **14 tests, OK** |
| Full Django suite | **1634 tests, OK**, 5 skipped |
| pytest regression (4 CI files) | **217 passed**, 2 skipped |
| `manage.py check` / `--deploy` / `makemigrations --check` | clean |
| Frontend `tsc --noEmit` | clean |
| Frontend ESLint | 18 errors / 17 warnings — **identical to the baseline at `4307fd0`** |
| Frontend `next build` | compiled successfully |

### What is NOT proven

- **No Outlook client has been tested.** Not one. `docs/OUTLOOK_ACCEPTANCE.md`
  has a row per Outlook variant and every row is blank. Automated tests prove
  the XML is correct; they cannot prove a client requests it or uses it.
- The endpoint has never served a request outside the test suite.
- `autodiscover.matemail.online` has no DNS record, no certificate, no vhost.
- No customer domain has published the SRV record.
- The SPF change has not reached any customer's DNS.

### Known limitation, recorded rather than fixed

`frontend/app/app/onboarding/page.tsx` still builds its own list of DNS records
as literals rather than reading `/api/domains/{id}/records/`. The SPF value in
it was corrected, but MX, DKIM and DMARC are duplicated there and can drift
from the backend again. Making onboarding read the API is the real fix and was
out of scope for this phase.

---

## PostBox remote push — MateMail server half (2026-09-26)

**Built and tested locally, and measured against the real pinned Dovecot
image. Not deployed.** No Firebase project, no Entra ID app registration and
no provider credential exists, so no real push has been sent. The PostBox-App
client half is not built; its contract is `docs/POSTBOX_REMOTE_PUSH.md` §12.
Decision: DEC-058.

### What exists

| Area | State |
|------|-------|
| Dovecot post-commit hook: `push_notification` + Lua driver, LMTP only, 1 s, fail-open | Built; measured in the pinned 2.4.1 image |
| Native API `POST /v1/dovecot/push` (own secret) + non-blocking relay to MateMail | Built |
| MateMail ingest `POST /api/internal/postbox/push-events/` (own secret, strict, stores once) | Built |
| Device API `GET/POST /api/postbox/devices/`, `DELETE /api/postbox/devices/<id>/` | Built |
| Session-bound registrations; revocation deletes them | Built |
| Deterministic event ids, dedupe, Celery dispatch, bounded retries, sweep, 7-day prune | Built |
| FCM HTTP v1 adapter (`google-auth`), data-only | Built; **mocked HTTP only** |
| WNS raw adapter (Microsoft Entra ID) | Built; **mocked HTTP only** |
| PostBox-App FCM receiver / WNS channel | **Not built** (next task) |

### Measured, not assumed

Against the MateMail derivative of `dovecot/dovecot:2.4.1@sha256:1296e0f1…`,
locally, with synthetic mailboxes:

- **Relay target up.** LMTP 250 in 0.07–0.41 s. The report held exactly
  mailbox, folder, UIDVALIDITY and UID, and they matched
  `doveadm mailbox status`. No subject or body marker ever left Dovecot.
- **Relay target down or stalled.** LMTP 250 at 1.08 s, the message saved, and
  one warning line.
- **Not a delivery.** `doveadm save` produced no event.
- **The production configuration.** `dovecot.conf` with the real entrypoint,
  push on and push off, starts for imap and lmtp. The rendered include is
  0600 root, and quota stays loaded for LMTP (`mail_debug`).
- **Two fixes came from measuring.**
  - The include is now `!include_try`: with `!include`, the new config on an
    older image was **fatal**, taking IMAP and LMTP down.
  - Revocation now deletes a session's registrations. They used to sit
    unselected, token and all, until the session was pruned.

### Tests

| Suite | Result |
|-------|--------|
| `tests.test_postbox_push` (new) | **25 tests, OK** |
| `tests.test_native_engine_push` (new) | **7 tests, OK** |
| Full Django suite (`manage.py test`) | **1826 tests, OK**, 5 skipped |
| pytest-only regression files (the four CI names) | **217 passed**, 2 skipped |
| `manage.py check` / `check --deploy` (prod) / `makemigrations --check` | clean / clean / no changes |

A local-harness note worth keeping: on Windows the bash-driven tests
(`test_backup_restore`, `test_deploy_revision_sync`) fail when `bash` resolves
to WSL's `bash.exe`, which cannot see `/c/...` paths. With Git Bash first on
`PATH` they pass. CI runs on Linux and is unaffected.

### What is NOT proven

- No push has been sent through FCM or WNS. Both adapters are verified against
  the providers' documentation and mocked HTTP only.
- Nothing is deployed. It needs new Native API and Dovecot images, the three
  push secrets and the MateMail release with migration `postbox 0005`.
- No PostBox app has received a push. The client half does not exist yet.
- WNS additionally needs Microsoft's Package Family Name → Azure AppId
  mapping, which is a manual request.
