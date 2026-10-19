# Phase 7C-10 — Recoverable IMAP outbox normalization and operator visibility

**Date:** 2026-10-19  
**Feature branch:** `feature/phase7a-auth-transport-foundation`  
**Status:** Isolated PostgreSQL, Native API HTTP and unit acceptance PASSED. Production activation NOT approved.

## Bug: valid mixed-case mailbox address broke durable revocation

The Native mail-client credential/policy handlers normalize and validate mailbox
addresses using `validation.email_address()`, but the durable disconnect
outbox initially queried `public.mailbox.address` using the raw request string.
A user-provided address such as `  ALICE@ISOLATED7C.EXAMPLE.TEST  ` could
therefore be authorized and mutated by the credential handler but fail to
enqueue the required disconnect, causing the entire security mutation to
roll back if `NATIVE_CLIENT_KICK_RECOVERY_ENABLED=True`.

**Remediation:** the outbox uses exactly the same validated canonical mailbox
address as the Native credential handler and Dovecot administrative kick.
Invalid mailbox identifiers fail closed before attempting an outbox query.

**Evidence:** negative unit test reproduced the incorrect raw address query
before the fix. Following the fix, all **68/68** Native Engine tests passed,
including the new address normalization and invalid-ID tests. Against **real
isolated PostgreSQL**, canonical insertion, exact mailbox association,
generation rotation, stale-ack fencing and repeat migration v15 were verified.
Logs:
- `results/stage7c-outbox-address-red.log`
- `results/stage7c-outbox-address-green.log`
- `results/stage7c-normalized-recovery-real-pg.log`

## Operator-only recovery backlog visibility

A durable retry worker needs an **observable** backlog: after an outage or
worker-disable rollback, pending session disconnect jobs must not silently
disappear from the operator's view.

Implemented a **read-only** internal Native API GET route:
`/v1/mailboxes/client/recovery/status`.

- Requires the regular Native API internal control secret. Unauthorized calls
  return HTTP 403 *before* making a database query.
- Queries only the outbox aggregate: `enabled`, `pending`, `due`,
  `max_attempts`, `oldest_pending_seconds`.
- Never returns mailbox ID, address, username, password, generation or
  raw exception text. No public port, read/write, session kick or queue
  mutation is created by this route.
- Even with the recovery worker flag OFF, the pending counters are reported.
  Operators can see stranded jobs after a rollback or while feature activation
  is suspended. `GET /health` and `GET /ready` behavior is unchanged;
  an outbox backlog does not by itself mark the whole mail service down.

**Evidence:** additional negative/positive operator authorization and payload
unit tests passed, bringing the full Native suite to **72/72 PASS**, log
`results/stage7c-recovery-observability-native-all.log`. On real isolated
PostgreSQL, the aggregate read worked with recovery disabled and with a
temporary process-local enabled flag (no worker launched), and returned only
counts; `results/stage7c-recovery-aggregate-real-pg.log`. After restarting
only the isolated Native API container, a real HTTP 403 without the secret
and HTTP 200 with the synthetic lab secret were confirmed, with no identity
details; `results/stage7c-recovery-operator-http.log`.

## Rollback and release restrictions

- Outbox migration 015 remains **additive** and was re-applied idempotently
  against isolated PostgreSQL. Do **not** delete its table or pending entries
  when rolling back API code.
- Never interpret a successful status-read or simulated successful Dovecot
  response as proof that real live IMAP connections were disconnected.
- `Require MFA` remains activation-locked. Native Recovery and other MFA
  flags remain OFF in production. No changes to production `main`.
- Still pending: combined Native-issued credential revocation → already-open
  Dovecot IMAP connection termination, real Dovecot outage and recovery with
  the enabled worker, complete customer mail-client acceptance, actual GitHub
  CI, supply-chain checks, backup/restore and user-approved production canary.
- All TestLab files, synthetic database evidence and logs must be preserved
  until Phase 8 cleanup.
