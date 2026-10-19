# Phase 7C-6 — Durable Mail Client Session Disconnect Recovery

**Date:** 2026-10-19  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Status:** Isolated persistence, restart and failure-contract acceptance PASSED. **Not a production release approval.**

## Problem and implementation

A Native API password revoke / client-access policy mutation commits in PostgreSQL before the Dovecot admin kick. If the kick fails or the API exits after COMMIT, already-authenticated IMAP sessions may remain connected until a manual repeat. A 503 warns callers, but is not an unattended recovery mechanism.

A new **mailbox-scoped PostgreSQL outbox** gives each committed security mutation a durable pending disconnect. This is not a bulk-session-disconnect API.

- Add migration 015 `public.mail_client_kick_pending`, one row per mailbox, ON DELETE CASCADE, unpredictable generation UUID, bounded retry counter and next-attempt time.
- Lock and serialize client credential/policy mutation first, then enqueue the outbox record **inside that exact database transaction**. Outbox errors roll back the mutation.
- After COMMIT, synchronously request the exact mailbox Dovecot kick. On success, clear only the generation committed by this mutation; failure retains the record.
- If Dovecot disconnect fails after COMMIT, preserve structured HTTP 503 `mail_client_disconnect_unverified`. A pre-commit failure must never claim saved credentials.
- Background worker polls pending due rows, acquires a transaction-scoped row lock with PostgreSQL `FOR UPDATE SKIP LOCKED`, retries the exact mailbox and acknowledges only its generation. On failure, the transaction records bounded exponential backoff (up to 60 minutes). There is no max-attempt deletion or silent loss.
- Worker startup is guarded by migration readiness, enabled credential lifecycle and **NATIVE_CLIENT_KICK_RECOVERY_ENABLED=True**. It remains OFF unless explicitly enabled. The feature flag and other Native MFA flags are passed through Compose, all default OFF.
- If Native API or Database restarts, pending jobs survive **provided the production PostgreSQL data volume persists**. No external broker or separate privileged credentials are required.
- Permission boundary: only `engine_api_runtime` can modify/read this queue. Postfix/Dovecot have no direct table privileges; the migration bootstrap writer retains ownership.

## Verified acceptance

1. Network-disabled, read-only Native API Python suite: **66/66 PASSED**. Includes HTTP transaction sequencing, outbox enqueue/ack, generation fence, empty queue, bounded backoff, worker error resilience, failed synchronous kick and acknowledgement-failure handling.
2. **Real isolated PostgreSQL 16:** Migration 015 installed, `GET /ready` reported `schema_version=15` and `ready=true`. Runtime role table privileges true; Dovecot/Postfix table SELECT privileges false.
3. Real PostgreSQL outbox: nine behavioral checks passed for commit, rollback, generation rotation, stale acknowledgement, preserved failed retry, persisted exponential backoff, successful simulated recovery and unrelated mailbox isolation.
4. Restarted **only** the disposable mm7cnative Native API container: queued synthetic mailbox was present before and after restart. A background worker using a **mocked successful Dovecot administrative response** removed only that mailbox's pending row. No real kick was performed in this test.
5. Unit suite was rerun after adding negative queue-failure and acknowledgement-failure cases: **66/66 PASSED**.

Evidence retained under `/opt/TestLab/MateMail-Stage7A/results/`:
- `stage7c-durable-recovery-unit-final.log`
- `stage7c-kick-outbox-postgres.log`
- `stage7c-kick-worker-after-restart.log`

Synthetic scripts retained under TestLab `test-data/`.

## Release gates that are **NOT** closed

- The combined **Native credential revoke/policy -> real already-established Dovecot IMAP session termination** on the same isolated stack remains unverified after prior tool safety restrictions. **Do not** treat mocked success as proof of live session termination.
- Real Dovecot outage and recovery with the **enabled** built-in worker has not been independently accepted on a complete Native/Dovecot stack. Current acceptance verifies real persistence and mocked transport, not live email-client disconnection.
- Organizational Require MFA activation guard, full cross-surface policy enforcement and production migration/key continuity need separate acceptance.
- Final GitHub CI checks, artifact digest and supply-chain review, production rollback rehearsal, controlled canary and user approval are still pending.

## Activation & rollback precautions

- Do not enable `NATIVE_CLIENT_KICK_RECOVERY_ENABLED` by itself. Credential lifecycle must be enabled, database at schema 015, internal Dovecot admin access working, and rollback tested.
- Existing SMTP/IMAP accounts and legacy authentication are unchanged by migration 015. It only adds an internal table and does not publish ports.
- Never flip `Require MFA` during code rollout; its independent release gate remains locked.
- A rollback to an older API image must retain the additive outbox table and must **not drop pending jobs**; operators must assess any pending disconnect jobs and session exposure before rolling back. A disabled worker cannot reconcile pending jobs.
- Maintain immutable deployment image digests and verified Fernet/key mounts. Do not deploy to production without explicit approval.
- Preserve ALL TestLab files and evidence until Phase 8 cleanup.
