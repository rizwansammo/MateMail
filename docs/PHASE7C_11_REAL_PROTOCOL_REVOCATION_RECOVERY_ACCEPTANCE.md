# Phase 7C-11 — Real Native SMTP/IMAP Revocation and Recovery Acceptance

**Date:** 2026-10-19  
**Feature branch:** `feature/phase7a-auth-transport-foundation`  
**Status:** Real isolated protocol and Dovecot admin-recovery acceptance **PASSED**.
**Scope boundary:** Synthetic TestLab only; no production changes or public ports.

## Isolated test architecture

- PostgreSQL 16 and Native API with schema version 15.
- Dovecot 2.4 Native mail-client authentication and its HTTP admin API.
- Postfix SMTP submission using Dovecot authentication.
- Docker internal-only network: `mm7cnative_isolated`.
- Only disposable `isolated7c.example.test` mailboxes and test credentials.
- All services are run using the existing, pinned test images and TestLab
  Compose files. No production images, volumes, active accounts, or secrets
  are mounted or used.

## A — Real IMAP/SMTP policy and authentication acceptance

Existing `test-data/stage7c-combined-smoke.py` confirmed successful legacy
Alice/Bob IMAP and SMTP login and denied wrong password, and Native API list
readiness. Existing `stage7c-native-credential-smoke.py` verified that a
Native-issued dedicated password authenticates through real Dovecot IMAP and
Postfix SMTP but cannot masquerade as a primary PostBox password.

New `test-data/stage7c-native-policy-protocol-acceptance.py` exercised **11
paired scenarios (22 IMAP/SMTP assertions)** on the same isolated stack:
1. Native-issued dedicated client password accepted.
2. Existing legacy primary password accepted before enforcement.
3. Dedicated-only policy denied legacy primary.
4. Dedicated-only policy permitted Native-issued password.
5. Unrelated Bob mailbox remained available.
6. Client-access-disabled policy denied dedicated password.
7. Client-access-disabled policy denied legacy primary.
8. Bob remained available during Alice's client-access disable.
9. Restored dedicated-only policy permitted issued password.
10. Actual Native revoke denied fresh IMAP and SMTP logins.
11. Bob remained available after revoke.

Result: **all 11 paired scenarios PASS**. Log:
`results/stage7c-native-policy-protocol-acceptance.log`.

## B — Actual already-authenticated IMAP session termination

New `test-data/stage7c-live-session-revocation.py` established **two
simultaneous, authenticated real IMAP connections**, using a newly Native-issued
synthetic dedicated credential for Alice and ordinary valid credentials for Bob.
It then called the **actual Native HTTP revoke API** for Alice, while both
connections were open:

- Native revoke returned HTTP success for committed credential state.
- The *previously authenticated* Alice IMAP connection was terminated by real
  Dovecot's mailbox-scoped admin kick.
- Bob's simultaneously established IMAP session continued to respond to NOOP.
- Reusing Alice's revoked password on a new IMAP connection failed.

Result: **all assertions PASS**. Log:
`results/stage7c-live-session-revocation.log`.

This closes the previously outstanding **real Native revoke → already-open
IMAP connection termination** acceptance gate on the isolated full stack.

## C — Real HTTP admin outage and automatic durable reconciliation

A **TestLab-only** Compose overlay enables the recovery flag and selects a
Dovecot admin URL without changing Dovecot itself. Initial Native API was
recreated with an intentionally unreachable admin URL on loopback; Dovecot
and Postfix remained alive and no production service was interrupted.

- A newly issued synthetic client password was revoked via real Native HTTP.
- Native returned **503 `mail_client_disconnect_unverified`** because the
  administrative disconnect was unavailable, while the *PostgreSQL revoke was
  committed*.
- The matching mailbox-scoped durable outbox job persisted, and the enabled
  worker logged **at least three retry attempts** in PostgreSQL.
- Only the disposable Native API container was recreated with its correct
  Dovecot admin URL and the worker still enabled; PostgreSQL/Dovecot/Postfix
  were left intact.
- The pending job survived restart and was **automatically acknowledged and
  removed** by the real recovery worker against the real Dovecot admin HTTP
  endpoint.
- Unrelated Bob mailbox still authenticated over real IMAP afterwards.

Results: **outage/commit PASS; recovery/worker PASS**. Logs:
`results/stage7c-worker-real-outage.log`,
`results/stage7c-worker-real-recovery.log`.

Important limit: this test isolates an **administrative HTTP connectivity
outage**, not a full Dovecot daemon/process outage. The concurrently open
IMAP session termination was accepted in test B; test C verifies the retry
and durable reconciliation with a real administrative endpoint after restart.

## Remaining mandatory release gates

- Full cross-surface organization Require MFA acceptance, especially enforced
  SMTP/IMAP dedicated credentials, recovery and legacy-client compatibility.
- Actual GitHub Actions CI checks and artifact/supply-chain validation on the
  release candidate; GitHub workflow status is not yet confirmed green.
- Production-owned encryption key mounts, schema migration/backups, rollback
  plan and controlled canary with explicit user approval.
- `can_activate_require()` remains deliberately locked; all production MFA
  flags remain OFF. Do NOT use these synthetic tests as deployment approval.
- Keep **all** TestLab files, scripts and evidence until Phase 8 cleanup.
