# Stage 7B — Hub and PostBox Security UI

Status: **Initial integrated UI implementation**. Feature branch only; no production deployment.

## Hub
- Workspace Settings: live, organization-scoped 2FA policy viewer/editor with Off / Encourage / Require; actual server-side readiness gates honored, Require shown unavailable until release gate is enabled, owner/admin changes require Hub password.
- Account Security: independent Recovery Email registration (primary mailbox disallowed), one-time 6-digit email verification, current password and existing authenticator reauthentication, verified address state.
- Account 2FA Disable: current account password plus current authenticator/backup code, respects server-side Require restriction.
- Hub Login: password-proven required-enrollment challenge, authenticator setup and one-time recovery codes.
- Hub Login Recovery: verified independent recovery-email OTP challenge without Hub JWT, tied to password-proven partial token. Authenticated session only established after verification.

## PostBox
- Security Settings: dedicated SMTP/IMAP Password List/Create/Revoke with one-time secret reveal, explicit per-mailbox client access toggle, current mailbox password and current authenticator code. The existing password/session UI remains unchanged.
- Mailbox 2FA: authenticator enrollment, backup codes and initial SMTP/IMAP Password one-time disclosure, voluntary disable with fresh password + TOTP; organization Require remains server-enforced.
- PostBox Login: 2FA-required challenge, enrollment and verification, recovery codes/initial client password disclosure before navigating to Inbox; session hydration follows successful server authentication.
- Public mailbox client cannot use Hub JWT: PostBox requests use its HttpOnly cookie.

## Validation
- TypeScript `tsc --noEmit`: passed.
- ESLint on modified UI: 0 errors; the existing full-file lint warnings are documented separately.
- All files developed under `/opt/TestLab/MateMail-Stage7A/source/` at this checkpoint; production remains on main.

## Still needed in Stage 7B before closing
- Browser-based UI acceptance: responsive light/dark, real challenge dialogs, one-time secrets accessibility, keyboard/tab order, error states, and mailbox switching.
- Full frontend production-build verification in a bounded CI/test runtime (avoid competing with MateServer mail service).
- Test the integrated UI against gated Stage 7A APIs using disposable end-to-end browser sessions.
- Review UX and server-side permission/rollback boundary cases with realistic disabled/enabled flags.

## Later Stage 7C
Production key mounting, public TLS SMTP 587 and IMAPS 993, controlled canary rollout, existing mail client and organization/tenant compatibility, and production acceptance. No 7B UI can bypass default-OFF backend security flags or activate Organization Require by itself.


## Integration hardening and validation — 2026-10-18

- Resolved important security-state ambiguity: the Native Engine's
  `dedicated_required` is **not** evidence that mailbox TOTP is enrolled.
  `GET /api/postbox/security/smtp-imap/` now returns `two_factor_enabled`,
  `organization_policy`, and authenticated `mailbox_address` explicitly.
  A team-box switch does not change which principal's credentials are managed.
- Credentials require a fresh authenticator code according to actual mailbox
  enrollment status, not inferred from protocol policy. Organization-required
  mailbox TOTP disable remains blocked by the backend.
- Temporary errors retrieving the credential list no longer destroy
  a newly issued one-time credential or backup-code display. UI explicitly
  identifies unavailable controls and permits retry instead of inventing state.
- Added `backend/tests/test_phase7b_security_status.py`: GET principal,
  separate policy/enrollment state, and default-OFF feature gate. Together
  with Phase 7A PostBox security suite **11/11 tests passed** against isolated
  disposable PostgreSQL with locmem cache.
- Frontend `tsc --noEmit` **passed**, modified security client component
  ESLint **passed** and `git diff --check` **passed**.
- Full **Next.js 16.3.8 Turbopack production build PASSED** in a resource-capped
  separate Docker container (1.5 GiB, 1 vCPU, no published ports). Includes
  all Hub, Platform and PostBox routes. An optional `--webpack` mode build
  encountered a static prerender AuthProvider failure; normal configured
  Turbopack build succeeded. Existing middleware-to-proxy deprecation warning
  remains independent of these UI changes.
- Only synthetic test mailboxes and disposable cache/db were used. The
  production frontend image and main branch were untouched.

**Not yet fully UI-accepted:** Browser-driven responsive/light/dark review,
keyboard/a11y and complete HTTP+cookie 2FA challenge interaction on the new
frontend against an isolated backend with feature flags ON. Do not label
Stage 7B closed until verified; all production feature flags remain OFF.


## Stage 7B browser acceptance — completed, 2026-10-19

**Browser:** Chromium Headless 141 on MateServer, using an entirely
TestLab-local dependency bundle (no Ubuntu packages installed).

**Topology:** Source branch built with Next.js 16.3.8 Turbopack. Disposable
Next server ran in a resource-limited Docker container, bound solely to
`127.0.0.1:31477`; all browser API requests were intercepted using
synthetic responses with no live mailbox or customer tokens.

- Browser interaction suite: **9/9 passed** across Hub/PostBox login,
  authenticator enrollment, invalid TOTP rejection, verification, one-time
  recovery code retention, recovery email login and registration,
  organization policy change and Require lock, dedicated SMTP/IMAP password
  creation/one-time reveal, disabled-feature fallback.
- Responsive/light/dark smoke checks: Desktop (1280), Mobile (390), no
  horizontal overflow in tested views. More device-specific visual review
  remains possible but is not a backend security gate.
- Axe-core WCAG A/AA checks: **0 violations** in the three selected
  authentication scenarios (PostBox desktop light, PostBox mobile dark,
  Hub 2FA). Initial contrast issues were corrected with narrowly scoped
  color changes in `frontend/app/auth-ui-accessibility.css`, imported
  by the root layout. Original Global/Premium stylesheets stay unchanged.
  No approved layout geometry
  or branding was changed.
- Full configured Turbopack production build: PASSED after CSS fixes.
  TypeScript/ESLint and Django mailbox status regression previously passed.
- Browser tests and screenshots preserved under
  `/opt/TestLab/MateMail-Stage7A/browser-e2e/` and `results/stage7b-browser/`.

### Stage 7B release decision

**Stage 7B implementation and synthetic Browser UI acceptance are CLOSED.**
This is not permission to activate 2FA/security feature flags in production.

**Stage 7C** MUST verify the UI with a real, flag-enabled,
**isolated backend** across HTTP/session/cookie boundaries; validate real
public STARTTLS/IMAPS, existing client compatibility, key continuity, session
revocation under outages, rollback, monitoring and controlled production canary.
No claim of real backend UI E2E or production acceptance is made here.

Feature branch is not merged or deployed. All test services shut down after
acceptance, while reproducible scripts/screenshots remain only in TestLab.
