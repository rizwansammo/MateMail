# Phase 7C-5 — Native revocation commit-boundary contract

**Date:** 2026-10-19
**Branch:** `feature/phase7a-auth-transport-foundation`
**Scope:** Native Engine loopback HTTP security error contract.
**Status:** Isolated HTTP/unit acceptance passed. Combined real IMAP session kick still pending.

## Finding

The Native API handled both database mutation and post-commit Dovecot administrative
disconnect in one `try` block. An `operations.OperationUnavailable`
exception raised by the write handler, or during transaction commit, could enter
an exception handler that replied with
`mail_client_disconnect_unverified`. That code means the password/policy
change **was committed**, but the active IMAP disconnect could not be verified.

This is an unsafe partial-success claim for a pre-commit failure: callers might
believe an exposed credential is revoked while the database mutation was rolled
back. The Django adapter intentionally treats this exact code as proof of
committed security state.

## Fix

- Track whether the Native API database context manager exited successfully,
  before making a per-mailbox administrative disconnect request.
- Report the precise partial-commit code **only** if the transaction completed
  and a subsequent administrative disconnect failed.
- Earlier `OperationUnavailable` cases return ordinary HTTP 503, never the
  saved-credential claim. No additional disclosure of credentials or keys.
- The existing security ordering (database change before session kick),
  mailbox-specific scoping, idempotent retry semantics and feature flags remain
  unchanged.

## Evidence

Real loopback HTTP server tests simulate transaction and administrative
dependencies; there is **no** production Dovecot session kill.

- Before fix: 2 negative tests failed as designed, showing the same structured
  partial-commit error in pre-commit and transaction-commit failure cases.
- After fix: Native Engine full suite **53/53 PASS**, including 2 new tests.
- Logs retained:
  - `results/stage7c-precommit-contract-red.log`
  - `results/stage7c-precommit-contract-fixed-native-all.log`
- Production, `main`, and activation flags were not touched.

## Unaccepted release gates

This is **not** proof of the combined real Dovecot/Postfix IMAP session
revocation test. Durable unattended reconciliation of failed disconnects,
Require MFA activation, and operator-approved production rollout remain open.
Preserve all TestLab artifacts for cleanup only after Phase 8.
