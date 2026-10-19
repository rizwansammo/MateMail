# Phase 7C-4 — Tenant-bound refresh session hardening

**Date:** 2026-10-19
**Feature branch:** `feature/phase7a-auth-transport-foundation`
**Status:** Isolated backend acceptance PASSED; overall Phase 7C NOT RELEASE COMPLETE.

## Security findings

1. A signed refresh cookie with a tenant ID that no longer exists reached
   `effective_two_factor_policy(None)`, resulting in HTTP 500 instead of a
   secure rejection. The refresh endpoint now clears the cookie and returns 403.
2. A user removed from an organization could still rotate a previously issued
   MFA-verified refresh token, despite losing access to protected API routes.
   The endpoint now verifies current ACTIVE tenant membership before rotation.
   It blacklists the old refresh token on revocation, clears its cookie and
   returns HTTP 403. Re-adding the membership cannot resurrect the old token.
3. Valid MFA-verified refresh cookie rotation continues to preserve proof.
   Previously consumed refresh tokens are rejected on replay. A pre-MFA refresh
   cookie cannot cross an activated Require policy.

No changes were made to the intentionally locked `can_activate_require()`
release gate or to deployed production Feature Flags.

## Test evidence

Isolated testing used the preexisting MateMail TestLab, a disposable PostgreSQL
database, disposable Redis, a source-mounted backend runner, and synthetic users.
The `DATABASE_URL` and `REDIS_URL` were overridden for TestLab only; existing
deployment environment files and production database/cache were not touched.

- Focused red tests reproduced a 500 (missing tenant) and a 200 (revoked member
  refreshing a previous MFA-verified token) before correction.
- Require MFA + independent recovery email + organization policy + API key
  security + live Django HTTP / PostBox adapter: **65/65 passed**.
- Existing refresh cookie tests, general authentication security, and Platform
  Admin authentication: **71/71 passed**.
- Native Engine 51/51 and browser MFA acceptance remain separately documented.
- Evidence:
  - `results/stage7c-refresh-red.log`
  - `results/stage7c-refresh-membership-red.log`
  - `results/stage7c-refresh-combined-acceptance.log`
  - `results/stage7c-refresh-platform-regression.log`

## Outstanding release gates

- Combined native credential policy/revoke and live Dovecot active-session
  disconnect proof on one isolated stack remains **unverified** (prior
  test execution was safety-blocked). Do not count mocked transport as proof.
- Durable retry/reconciliation after a failed Dovecot session disconnect.
- Independent complete Require MFA cross-surface acceptance and its intentional
  release activation gate.
- Full key-continuity, compatibility, migration, rollback and operator-approved
  production canary. No `main` merge or production activation.

Keep all TestLab files, browser downloads and evidence until Phase 8 cleanup.
