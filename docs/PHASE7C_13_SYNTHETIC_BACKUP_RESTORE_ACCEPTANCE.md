# Phase 7C-13 — Require MFA Challenge Boundary and Synthetic Backup/Restore Acceptance

**Date:** 2026-10-19  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Scope:** Feature-only Django hardening and isolated Native DB disaster-recovery rehearsal. **No production change.**

## A. MFA challenge issued before tenant membership revocation

The existing 2FA verification checked tenant membership *after* TOTP/backup-code
verification and challenge consumption, and denied missing membership only
when a dedicated tenant host was being used. On the shared host, a valid
OTP could still mint an MFA-proven, tenant-scoped access JWT after the
tenant membership was REMOVED. Subsequent protected APIs denied the token,
but it should never have been issued.

**Fix:** Immediately after checking password fingerprint and before checking
any OTP, backup code or issuing tokens, resolve the original challenge's
current active tenant membership and host scope. If revoked, discard the
single-use challenge and return HTTP 403 without a JWT, refresh cookie or
consumed backup code. This also covers the shared host.

**Evidence:** Two new negative tests reproducing an already-issued challenge
followed by a membership revoke. Before the fix, the TOTP test received HTTP
200 with an MFA-proven access token. Following the correction, isolated
PostgreSQL + Redis Django acceptance across organization Require MFA,
2FA replay/brute-force, login recovery, auth, refresh, invitations and
PostBox: **98/98 PASS**.

- `results/stage7c-challenge-membership-red.log`
- `results/stage7c-challenge-membership-combined.log`
- Source: `backend/apps/accounts/views.py`,
  `backend/tests/test_required_organization_2fa.py`
- Detailed security rationale:
  `docs/PHASE7C_12_MFA_CHALLENGE_MEMBERSHIP_SECURITY.md`

## B. Isolated migration v15 synthetic backup and disaster-recovery rehearsal

The entire exercise used only the disposable `mm7cnative` PostgreSQL 16
container, internal Docker network and `isolated7c.example.test` synthetic
mailboxes. Absolutely no production storage, credential, database or volume
was accessed.

1. Started isolated PostgreSQL and Native API and applied schema migrations
   to version **15**.
2. Seeded two synthetic customer mailboxes and a synthetic security state:
   **one pending mailbox disconnect** and **one client-access-disabled policy**
   requiring dedicated credentials.
3. Created PostgreSQL custom-format backup (synthetic only), retained as:
   `results/stage7c-synthetic-schema-v15-backup.dump`.
4. Created an entirely independent database `mm7c_restore` in the *same
   disposable server*, restored the backup with `pg_restore --exit-on-error`
   and compared relevant state.

**Source and restored database state matched:**
`schema_version=15`, `pending_disconnects=1`,
`disabled_dedicated_client_policies=1`, `synthetic_mailboxes=2`.

**Permission boundaries survived the restore:** Native API Runtime Role
retains SELECT on pending disconnects; Dovecot reader role does **not**.

**Result:** Backup and independent database restore **PASS**, with no
permission regressions. Evidence:
- `results/stage7c-release-restore-seed.log`
- `results/stage7c-schema-v15-restore-run.log`
- `results/stage7c-synthetic-schema-v15-backup.dump`
- Backup SHA-256:
  `19273cee4d43a6c34278156893d23915463dbb5dfa8685d0cbd7f9655411465f`

## C. CI execution strategy and remaining release gates

Only five workflows are configured to trigger for a Pull Request:
`astra-hub-visual-qa.yml`, `ci.yml`, `native-api-security.yml`,
`native-engine-phase2.yml`, `p3-frontend-runtime.yml`.
A feature-branch commit alone does not trigger normal main-targeted PR checks.
No PR has been opened or Github-hosted runners invoked in this stage, to
avoid running many resource-intensive jobs before the release candidate is
stable. **Remote GitHub CI remains unverified.**

**Pending before production:**
- Actual GitHub CI on a reviewed release candidate and artifact digest checks.
- Production-owned key persistence and **real production** backup/restore and
  rollback verification (no production modifications without approval).
- Staged customer compatibility/organization Require MFA activation with
  legacy-client behavior, and manual acceptance after user-approved canary.
- `can_activate_require()` remains locked `False`. No production feature
  switches, `main` merge, deployment or email-customer change made.
- Preserve ALL TestLab evidence and generated artifacts until Phase 8 cleanup.

Earlier isolated real Native/Dovecot session revoke and administrative
outage/retry completed in Phase 7C-11; this stage did not modify those paths.
