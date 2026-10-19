# Phase 7C-19 — PostBox MFA shared runtime readiness and encryption-key gate

**Date:** 2026-10-19  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Release status:** Source reviewed; GitHub PR CI pending for this new change. Production unchanged.

## Finding

PostBox had two conflicting activation predicates:

- `apps.postbox.two_factor_core.enrollment_ready()` always returned False, an
  early Stage 7A protection stub that was no longer called by the HTTP routes.
- `apps.postbox.two_factor_views._ready()` admitted real 2FA enrollment,
  verification, recovery and client-credential HTTP operations based on both
  security flags and the Native adapter, but did not check that the PostBox
  Fernet encryption key was configured and syntactically valid.

Thus the standalone named readiness helper could misrepresent the **actual
runtime HTTP gate**, and an enabled deployment with a missing/malformed
encryption key might permit operations far enough to fail unexpectedly
instead of reporting the normal unavailable response.

## Security-preserving change

- Make `enrollment_ready()` the single shared predicate used by
  `two_factor_views._ready()`. It returns True only when:
  - `POSTBOX_2FA_ENABLED is True`
  - `SMTP_IMAP_SECURITY_ENABLED is True`
  - `MAIL_ENGINE_ADAPTER == "native"`
  - `POSTBOX_2FA_FERNET_KEY` parses as a valid Fernet key.
- Invalid/missing/non-ASCII keys return False, causing a generic **HTTP 503**
  at the existing `_gate()` before MFA enrollment or any client-credential
  handling. No secrets or key material are surfaced by the response.
- Current API behavior when all flags and the key are valid is unchanged.
  No config switch can activate Organization **Require MFA**:
  `apps.tenants.security_policy.can_activate_require() == False` remains
  independent, unchanged and explicitly tested.
- Updated one synthetic Phase 7C partial-commit test fixture to specify its
  own ephemeral Fernet key, so it exercises intended downstream error handling
  under a valid, otherwise ready test environment. No real key is used.

## Regression tests

Added/updated tests to cover:
1. Both flags OFF, each individually ON, wrong Native adapter, and both flags
   ON with a valid encryption key; the shared core readiness and HTTP helper
   must agree in every case.
2. Missing, malformed and non-ASCII encryption keys rejected through the
   actual `POST /api/postbox/auth/2fa/enroll/` HTTP boundary with status 503,
   without disclosing an enrollment secret.
3. Organization Require MFA lock remains False even when the separate
   PostBox voluntary-enrollment prerequisites are true.

**Acceptance:** Python syntax and git whitespace check performed on the
source branch. Backend/CI functional results MUST be confirmed on the new
exact PR commit before declaring this stage accepted. No local alternative
route is used to bypass an earlier blocked TestLab automated test command.

## Unchanged release blockers

- Draft PR #136 remains NOT approved for merge or deployment.
- Production persistent distinct keys, immutable production image digests,
  current offsite backups, approved restore/rollback and limited canary are
  still unverified. Do not interpret successful source/CI checks as proof.
- No `main` merge, production key/flag/container/database changes or
  customer mailbox operations.
- Preserve **all** `/opt/TestLab/MateMail-Stage7A/` evidence until Phase 8 ends.
