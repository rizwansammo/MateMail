# Phase 7C — Controlled Integration, Compatibility & Production Rollout

**Status:** Started, isolated real HTTP integration tests in progress.  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Test root:** `/opt/TestLab/MateMail-Stage7A/` (all temporary artifacts below `/opt/TestLab`).  
**Production:** no changes, no main merge, no new DNS or public ports.

## C1. TestLab HTTP authentication contract — tested

The `tests.test_phase7c_live_http` suite uses a real Django LiveServer
with disposable PostgreSQL + Redis connected over a Docker `internal: true`
network. It sends real urllib HTTP requests through Django middleware,
authentication, URL routing, rate limiting, JWT/OTP and HttpOnly cookie
generation. Hub and PostBox have distinct identities.

**Result:** 7 / 7 tests PASSED. Tests prove:

1. An enrolled Hub user's password returns only an opaque partial challenge,
   not an access JWT or refresh cookie.
2. A partial challenge is not a bearer token; successful TOTP returns
   an access JWT and an HttpOnly, SameSite=Strict, path-scoped refresh cookie.
   Challenge replay is denied.
3. Even with Stage 7C policy flags enabled in the test process,
   organization `Require` activation is rejected (409) by the
   intentional backend release gate.
4. A Hub user's authenticated JWT does not authorize a PostBox mailbox.
5. The mailbox's wrong primary password does not create an MFA challenge.
6. With both PostBox/SMTP-IMAP feature flags off, the legacy mailbox
   authentication path continues to establish a session.
7. With mailbox MFA on, the legacy session is denied after enrollment;
   a password-proven partial challenge creates no session, and a
   single-use TOTP must complete before a Secure, HttpOnly
   `__Host-postbox_session` cookie authorizes mailbox access.

**Scope limitation:** The test uses a mocked Native primary password
verification function and mocked IMAP folder provisioning. All other
HTTP/API/authentication/database/cache/cookie boundaries are real.
It is not the final Native API/Dovecot/SMTP or real-browser-to-Django test.

## C2. Native Engine and mail-client compatibility — next gate

- Isolated Native API + Dovecot 2.4 + Postfix, using the exact candidate
  commit/images and synthetic test users. Never join production networks.
- Native password verification, dedicated credential create/revoke, and
  case-by-case IMAPS 993 / submission 587 AUTH over TLS; reject legacy
  mailbox passwords only after effective MFA is verified.
- Verify independent identity and operational master-user controls.
- Test live IMAP session disconnect through the Native API HTTP
  `doveadm kick`, outage returns truthful 503, and reconciliation/retry
  continues until sessions close. Verify unrelated mailboxes stay connected.
- Exercise disabled access, unauthenticated inbound SMTP, outbound relays,
  app/client reconnection, and system mailboxes separately.
- Confirm callback and secret mount contracts, migration/backfill, timeouts,
  rate limiting, failure isolation and tenant scoping.
- Do not declare integrated release ready based on Stage 7A unit mocks.

## C3. Frontend/browser and key continuity — subsequent gate

- Serve real Next.js Stage 7B UI against disposable feature-enabled
  Django + Native API, rather than intercepting API calls with fake JSON.
- Use headless Chromium to inspect actual HTTP and browser cookie behavior
  for PostBox and Hub at login, enrollment, recovery, error states and
  mobile responsiveness. Verify no credentials leak to console/logs.
- Validate encrypted Hub/PostBox TOTP keys across restarts and rollback
  without storing production secrets in TestLab.
- Rehearse an old-schema data migration and reversible rollback on an
  anonymized synthetic dataset; preserve backups and re-encryption evidence.

## C4. Controlled production readiness — explicit approval required

**No production action before the operator approves a written plan.**
Mandatory checklist:

- Final Feature Branch code review, CI and supply-chain checks.
- Confirm existing **Azure daily offsite backup** readiness, backup
  evidence and restore/rollback rehearsal. Do not propose a new VPS.
- Retain all current PostBox/web logins and existing SMTP/IMAP customers.
  Do not force any organization to enroll at the instant of deployment.
- Mount independent `HUB_2FA_FERNET_KEY`,
  `POSTBOX_2FA_FERNET_KEY` and Native secrets, without exposing contents;
  preserve keys in recovery.
- Deploy schema backward-compatible and flags OFF; verify legacy behavior.
- Controlled canary, limited scope and time with explicit rollback triggers;
  test public STARTTLS/IMAPS TLS with pre-existing devices and accounts.
- Unlock `Require` only after **full** policy enforcement, Hub/Platform/API
  key boundaries, recovery, old JWT rotation and Native mail-client
  enforcement have been independently validated. Current
  `can_activate_require() -> False` must not be bypassed.
- Keep the intentionally configured DMARC `p=none` policy untouched.

## Evidence and safety

- Command: `python manage.py test tests.test_phase7c_live_http --noinput`
  inside the pinned image with `--network mm7c_isolated`.
- No production credentials, mailboxes, mail volumes, or network connected.
- Containers capped: 256 MiB PostgreSQL, 64 MiB Redis and a 768 MiB test
  runner; database persisted only to a container-local tmpfs.
- Artifact log: `/opt/TestLab/MateMail-Stage7A/results/stage7c-live-http-tests.log`.
- Feature flag overrides only occur in the test class; production flags
  remain OFF. TestLab network/containers removed after testing.
