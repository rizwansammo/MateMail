# Phase 7C-8 — Require MFA invitation and signup session continuity

**Date:** 2026-10-19  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Status:** Isolated API and frontend regression checks passed; organization `Require MFA` activation remains locked.

## Finding

The preexisting invitation acceptance and invited-account signup flows issued a
normal Hub refresh-cookie/JWT session after membership creation—even when the
destination organization's **effective policy was Require MFA**. Such JWTs
lack verified-second-factor proof. The live tenant middleware correctly rejects
them, leaving a successful invitation/signup followed by an unusable session
and an unexpected failure opening the workspace.

The acceptance path must not invent MFA proof from an invitation, an already
authenticated JWT or a newly created account password.

## Changes

- Invitation acceptance continues to validate and consume the invitation,
  create the membership and, when explicitly requested, provision the mailbox.
  If the destination tenant's effective policy is `Require MFA`, the API
  returns HTTP 200 with `requires_login`/`requires_2fa` and **does not
  issue a new JWT or refresh cookie**. All other workspaces preserve their
  existing ordinary successful authenticated response.
- The already-member invitation acceptance branch uses the same conditional
  response.
- Invited-account signup returns HTTP 201 with the normal user/tenant/mailbox
  result but **does not issue an unverified session** for a Require tenant.
  Email verification continues; the user completes a fresh password login and
  the normal Hub authenticator enrollment/challenge before accessing the Hub.
- The Accept Invite page displays an explicit **Sign in to complete 2FA**
  action instead of trying to open the blocked workspace. Signup's auth context
  no longer stores an absent access token; the verify-email experience remains.
- Existing MFA enforcement and the intentionally locked
  `can_activate_require() -> False` are unchanged.

## Verification

- Added two negative API tests for Require-MFA invitation acceptance and
  invited-account signup. Before correction, both failed because the
  `requires_login` boundary was absent.
- Isolated Invitation + Require MFA + organization-policy test suite:
  **35/35 PASS** after correction.
- Wider isolated invitation, Require MFA, refresh-cookie, generic auth,
  PostBox and recovery-email suite: **87/87 PASS**.
- Frontend `tsc --noEmit --incremental false`: **EXIT 0**.
- The invitation suite's cache was isolated and reset between tests to avoid
  shared synthetic signup throttling state; production rate limits unchanged.
- Evidence logs: `results/stage7c-invite-require-red.log`,
  `results/stage7c-invite-require-green.log`,
  `results/stage7c-invite-full-auth-regression.log`,
  `results/stage7c-invite-frontend-tsc.log`.

## Still unaccepted

Combined real Native issuance/revoke -> live Dovecot active IMAP session
disconnect, enabled-worker real Dovecot outage/recovery, cross-client Require
MFA release verification, external compatibility, key continuity, rollback,
GitHub CI and user-approved production canary. These remain release blockers.

No production or `main` changes. Do not clean TestLab until Phase 8 is finished.
