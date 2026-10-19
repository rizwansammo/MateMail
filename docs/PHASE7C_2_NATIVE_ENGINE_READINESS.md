# Stage 7C-2 — Native API Isolated Security Readiness

**Date:** 19 October 2026  
**Status:** In progress; NOT Native Engine End-to-End Accepted  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Workspace:** `/opt/TestLab/MateMail-Stage7A/`

## Work completed in TestLab

- Prepared `deploy/compose.stage7c-native.yml` with synthetic PostgreSQL,
  source-overlay Native API, Dovecot 2.4 and Postfix images. All instances are
  connected only to an internal Docker network. No ports are published.
- CPU/memory/PID caps: PostgreSQL 256 MiB, Native API 200 MiB,
  Dovecot 256 MiB, Postfix 200 MiB. All data is synthetic; no production volume,
  credential, database or network is mounted.
- Real Native API startup migration completed to **schema 14**, in an isolated
  temporary database.
- Least-privilege `engine_api_runtime` role active with an independent
  synthetic 32+ character credential. First attempt intentionally failed
  closed on an undersized synthetic runtime password; corrected and rechecked.
- Native API `GET /ready` returned ready=true, schema_version=14.
- **6/6 real Native HTTP boundary checks PASSED**:
  authenticated readiness; anonymous rejection; missing/wrong API secret
  rejects client credential endpoint; unknown mailbox cannot be listed; trusted
  primary password check for unknown mailbox returns only `valid: false`.
- **14/14 Native Engine unit tests PASSED** in a separate 192 MiB,
  network-disabled container: seven Credential Lifecycle Guards and seven
  Active Session Revocation/Fail-closed Checks. These are unit tests, not
  actual Dovecot kick integration.
- **5/5 privilege conditions PASSED** in the actual isolated PostgreSQL:
  Dovecot and Postfix cannot directly SELECT `mail_client_password`;
  only Dovecot may execute `dovecot_client_auth(text,text)`; runtime role login
  is active. The SQL result was `t|t|t|t|t`.

### Evidence retained until Phase 8

- `results/stage7c-native-boundary.log`
- `results/stage7c-native-privileges.log`
- `results/stage7c-native-unit-credentials.log`
- `results/stage7c-native-unit-revocation.log`
- `test-data/stage7c-native-boundary.py`
- `test-data/stage7c-native-privileges.sql`
- `test-data/stage7c-native-fixtures.sql` (prepared but NOT applied)
- `deploy/compose.stage7c-native.yml`
- `deploy/stage7c-dovecot.conf`
- `deploy/stage7c-dovecot-db.conf`

## Additional Native credential security hardening (2026-10-19)

This follow-up fixed two issues identified during the Stage 7C-2 review:

1. **Unknown-mailbox timing equalization.** The Native trusted web primary
   password verifier now executes a dummy bcrypt check before returning its
   always-negative answer for unknown, inactive, or protected identities. This
   reduces an obvious timing distinction between missing and existing mailbox
   records. The decoy password can never authenticate any mailbox. This does
   **not** claim formally constant-time behavior over an entire network path.
2. **Per-mailbox credential lifecycle serialization.** Password issue and
   revoke now lock the specific mailbox database row before writes. The
   existing policy update already used this lock; all three operations now
   serialize so concurrent credential issuance cannot bypass the 10-active-
   credential maximum and policy update sees a consistent credential count.

- New focused tests: **4/4 passed**.
- Entire Native API Python unit suite (including old revocation and platform
  security tests): **48/48 passed** in a 384 MiB, network-disabled test
  container. This confirms unit behavior only, not the blocked full combined
  SMTP/IMAP run.
- Logs: `results/stage7c-native-hardening-unit.log` and
  `results/stage7c-native-all-unit.log`.
- All code changes remain on the feature branch. Release flags remain OFF.
- Stage 7C-2 combined Native/SMTP/IMAP E2E test remains pending under the
  separately documented safety blocker.

## Native API real loopback HTTP revocation integration — 2026-10-19

With the combined synthetic PostgreSQL/Dovecot/Postfix fixture still
unavailable, a new isolated real HTTP control-plane integration test
verifies the actual Native API HTTP handler over 127.0.0.1. The database
transaction and Dovecot administrative transport are explicitly mocked,
so this does not claim real IMAP/SMTP, SQL lifecycle or networked Dovecot.

- 3/3 new HTTP revocation tests passed: API-secret authorization;
  mutation commits before a mailbox-specific disconnect; a transient
  Dovecot outage returns 503 AFTER the committed revocation, and retrying
  the already-revoked idempotent request reissues the session kick.
- No cross-mailbox or wildcard disconnect route was added.
- 51/51 complete Native Engine Python tests passed, including original
  platform and credential tests and earlier four hardening tests.
- Evidence: results/stage7c-native-http-revocation.log and
  results/stage7c-native-all-unit-20261019.log
- Source: engine/native_api/tests/test_phase7c_http_revocation_contract.py
- No real customer account, production DB, live SMTP/IMAP connection
  or production administrative endpoint was accessed.
- C2 combined Native/SMTP/IMAP E2E remains pending. This test cannot
  substitute for it.

## Blocker: isolated synthetic mailbox population

The attempted test action that would initialize synthetic mailboxes and launch
the accompanying Dovecot/Postfix services was blocked by tool safety checks.
It was **not executed**, so Stage 7C-2 cannot yet claim:

- a live Native API `issue -> list -> policy -> revoke` lifecycle backed by
  a synthetic mailbox and actual PostgreSQL rows;
- new dynamic Native API-issued bcrypt passwords authenticating against real
  Dovecot IMAP and Postfix SMTP SASL in the **same** isolated environment;
- Native API -> doveadm HTTP active-session kick with a dynamically issued
  credential, including forced API outage/recovery;
- full 993/587 TLS compatibility in the staged candidate configuration.

Existing Stage 7A isolated Dovecot/Postfix authentication tests have separate
positive evidence but do not replace the missing Stage 7C-2 **combined** test.
Do not bypass safety restrictions by using production mailboxes or Production
Native DB.

## Production release gating

- Stage 7C-2 remains **IN PROGRESS**; no merge, deployment or feature
  activation is approved.
- Production host configuration, current SMTP/IMAP customer passwords,
  DKIM, external MX and existing Azure offsite backup are not altered.
- Feature flags must remain OFF; especially `Require` stays locked until
  verified full enforcement.
- Preserve all TestLab files, fixtures, downloaded browser dependencies and
  evidence. **Final TestLab cleanup only after Phase 8 closing**; test
  containers can be stopped now to release live RAM without deleting files.
