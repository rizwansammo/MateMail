# MateMail Phase 2 Validation Report

Date: 2026-10-12

Branch: `feat/platform-domain-ownership-phase2-20261012`

## Environment and scope

Validation ran against **isolated** PostgreSQL 16 and Redis containers on
MateServer, plus a disposable PostgreSQL 18 cluster for native SQL tests.
Production backend, Native Engine, domains, PostBox mailboxes and DNS were
not modified. Backend code was copied into a temporary test directory and
overlaid with the feature branch source; the production backend image itself
was not modified. The Native API image supplied test-only runtime dependencies.

## Evidence

| Gate | Result |
| --- | --- |
| Django check | Passed |
| Django makemigrations --check --dry-run | Passed: no model drift |
| Django domain ownership/deletion tests | 62 executed, all passed |
| Broad Django: PostBox, platform, TeamBox, Forward Group, isolation, ownership | 159 executed, all passed |
| Native engine provisioning module with isolated PostgreSQL 16 | 173 executed; no failures, four skips |
| New Native Engine guard tests | 6 tests, all passed |
| Real Native API on disposable PostgreSQL 16 | 10 protected operations denied, legacy mailbox login accounting succeeded |
| Native SQL migration 001–007 and trigger test (PostgreSQL 18) | Passed, including existing legacy domain and mailbox |
| PostgreSQL 16 Native API migration 001–007 | Passed |
| Python compile/static guard review | Passed |
| GitHub Actions runs started for Phase 2 | Zero |

These suites overlap and their counts must **not** be added as an overall unique
total. Skipped cases are not evidence of passing behavior.

## Code fix discovered during validation

`www.matemail.pro` was incorrectly canonicalized to `matemail.pro` before
the infrastructure hostname reservation check. The serializer now denies
reserved input hostnames before stripping the optional `www.` prefix.
A test also needed to preserve a Domain object's UUID before Django's
`delete()` clears its local primary key.

## Rollout blockers

1. A full project-wide Django/pytest CI run has not been performed. Relevant
   targeted suites passed. Do not describe CI as passing for this branch.
2. Django migration backfill, blocked platform-domain collision, reverse and
   reapply rehearsals all PASSED on a disposable PostgreSQL 16 database.
   A controlled two-database rollback procedure including Native Engine
   migration 007 still needs release-level verification.
3. Production `matemail-backup.service` remains in FAILED status because of
   three stale Restic locks dated 2026-10-10. There were no active Restic
   processes. A nonmutating `restic check --no-lock --quiet` returned 0, and
   snapshot 9ab0d872 included the manifest and both DB dump paths. No locks
   were removed, no service was rerun, and no restore was performed. A normal
   successful backup and actual restore drill are still required.
4. The controlled DNS Canary observation and unrelated mail security
   infrastructure must not be interrupted.
5. The Django registry deliberately retains a deleted domain's ownership
   claim, preventing unsafe re-registration. A verified cleanup-and-release
   workflow is not implemented in this branch; do not release claims by hand.
6. Native Engine migration 007 freezes mutations to protected identities.
   It requires a coordinated and separately approved rollout with the
   matching Native API image and Django migration; deploying only one half
   is unsupported.

## Promotion decision

**Candidate code and targeted QA completed. Main merge and production
deployment withheld until all release blockers are cleared.** No repository
visibility change, PR-triggered workflow, DNS update, system mailbox
provisioning, running production container replacement, or production database
schema change was performed as part of this QA.

## Incremental verification, 2026-10-12

The isolated Django migration test completed successfully with the following
ordered assertions: reject an existing verified tenant claim against
`system.matemail.pro` atomically (without leaving the registry table), apply
migration 0009 while preserving the unrelated customer's verified domain,
backfill ten reserved platform hostnames, reverse to 0008 while retaining
customer records, and reapply to 0009 with ownership unchanged.

The Restic repository has six snapshots, including 9ab0d872 dated
2026-10-12. The default `matemail-backup.service` failed its own integrity
check after creating the snapshot because three 2026-10-10 lock records
remain. With no running Restic processes, the independent read-only command
`restic check --no-lock --quiet` returned exit code zero. The snapshot's
`manifest.json`, `db/matemail.dump`, and `db/native.dump` were verified
present by read-only listing. This does not substitute for a restore drill,
and does not fix the service failure.

### Safeguards retained

- No production backup lock, timer, retention, snapshot, Maildir or encryption
  key was modified by this review.
- Stale lock removal must be a separately authorized and verified backup
  maintenance operation; no `restic unlock` was executed.
- Disposable Docker PostgreSQL 16 and its isolated network were removed after
  the successful migration rollback rehearsal.
- The Native Engine v007 rollback compatibility with an earlier API image
  and complete backup/restore recovery remain preconditions for release.


## Final pre-release restore drill and schema compatibility (2026-10-12)

Restic snapshot `ec92ebd9` was restored in full to a private temporary
`/run` tmpfs directory. No production Docker volume or application
configuration was mounted or overwritten. The test directory was removed
after verification.

- Restic full-repository `check --no-lock --read-data`: PASS.
- All 29 files tracked in the staged-file manifest passed SHA256 and byte-size
  comparison.
- 111 restored Maildir files matched the manifest count.
- All 12 restored DKIM private key files parsed with OpenSSL.
- The two restored PostgreSQL 16 databases loaded without `pg_restore`
  errors: MateMail archive 614 objects, Native archive 128 objects.
- Restored MateMail counts matched the snapshot manifest:
  3 tenants, 5 domains and 5 mailboxes.
- Restored Native counts matched:
  6 domains, 8 mailboxes, 1 retired storage entry, schema version 6.
- Native migration 007 applied successfully to the **restored test database**
  and reserved exactly 10 system/transport hostnames.
- The currently deployed older Native Engine API image, whose minimum required
  schema is 6, successfully read the upgraded test database at schema 7.
  Read-only SMTP/IMAP auth and lookup views were accessible (Dovecot auth 7,
  virtual mailbox 8, sender login 7). Legacy platform mailbox login timestamp
  bookkeeping remained writable.
- With the older Native API, protected platform domain reconciliation was
  denied and a synthetic customer domain could still be provisioned.
  This confirms the tested API/DB rollback compatibility, not an automatic
  schema downgrade.
- The disposable PostgreSQL 16 container, temporary test network, restored
  data (including copied credentials and DKIM keys), and test files were
  removed. One expired lock created by an interrupted read-only listing was
  removed after confirming no Restic processes remained. Subsequent Restic
  integrity check passed, with zero remaining locks.
- The normal scheduled Local Restic Backup remains enabled and the last
  manually executed backup service was successful.

### Release boundaries remaining

The full GitHub Actions CI workflow (including Node frontend build and image
publishing jobs) was deliberately NOT triggered: it runs on main or a PR
targeting main, with private-repository minutes and GHCR side effects. Relevant
isolated backend tests have already passed, but the entire CI matrix has
not been executed on this branch.

No production Phase 2 migration, merge, deployment, or live SMTP/IMAP
send/receive test has happened. A controlled production release still needs
the release owner's explicit approval, a current confirmed backup, a rollback
plan for the database and application images together, and live post-deploy
mail authentication/delivery checks.
