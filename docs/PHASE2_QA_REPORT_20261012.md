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
2. Django migration reverse/forward rehearsal has not been completed in an
   isolated test database. Test database forward migration succeeded.
3. Production `matemail-backup.service` previously failed its integrity
   check because of a Restic repository lock. Recovery is not yet re-verified.
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
