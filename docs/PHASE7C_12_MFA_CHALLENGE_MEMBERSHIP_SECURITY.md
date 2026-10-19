# Phase 7C-12 — MFA Challenge Membership Revocation Boundary

**Date:** 2026-10-19  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Status:** Isolated Django negative and combined regression acceptance PASSED. No production activation.

## Finding

A password-verified Hub 2FA challenge may already exist when the account's
organization membership is revoked. The existing MFA verification path checked
for a matching active membership **after** validating/claiming the TOTP or
backup code and consuming the challenge. On a shared host, missing membership
did not block token issuance at that final check.

The tenant middleware correctly denied access on the subsequent protected API
request, but issuing a new tenant-scoped, MFA-marked JWT after permission
revocation was nevertheless an incorrect authentication boundary. Backup-code
verification could also consume a valid backup code after membership loss.

## Fix

- After the challenge's password fingerprint is verified but **before**
  checking a TOTP or backup code, resolve the current ACTIVE membership for the
  original tenant claim and host binding.
- Reject and discard the challenge immediately with HTTP 403 if the tenant
  claim is no longer valid, including on the shared host.
- Do not mint an access JWT or refresh cookie, accept an OTP or consume a
  valid backup code for a removed member.
- Keep the established behavior for valid members and non-tenant challenges.
- Existing recovery-email login already checked membership before consuming
  proof; no change to that separate protocol.

## Evidence

- Added negative regression: a user starts a valid password+TOTP challenge,
  membership becomes REMOVED, then submits a correct TOTP. Before fix HTTP
  200 incorrectly returned an MFA-proven JWT. After fix HTTP 403, no access
  token/refresh cookie and original challenge discarded.
- Added backup-code regression verifying the removed member cannot exchange
  a challenge and the previously unused backup code remains unused.
- Isolated real Django HTTP suite with PostgreSQL and Redis:
  **98/98 PASS**, including Require policy, general 2FA hardening,
  recovery-email, auth, refresh cookies, invitations and PostBox 2FA.
- Evidence:
  `results/stage7c-challenge-membership-red.log` and
  `results/stage7c-challenge-membership-combined.log`.

## Remaining release gates

- GitHub Actions must run and report green against a PR/release candidate;
  feature branch commits alone do not trigger the primary CI workflows.
- Production-owned persistent MFA secret mounts, migration/backup/restore
  checks and reviewed rollback plan, client compatibility and operator-
  approved canary are still pending.
- The `can_activate_require()` activation barrier is intentionally locked.
  The real isolated Native/Dovecot revocation and admin recovery acceptance
  previously PASSED (Phase 7C-11); this change does not re-open them.
- No production/main changes. Keep all TestLab files through Phase 8.
