# MateMail Release 1 final isolated QA evidence

Date: 2026-10-13. Candidate: Phase 2 ownership protection plus Phase 3 dynamic
system mail backend. Production has NOT been merged or deployed.

## Verified on isolated MateServer containers

- Full branch (not a partial overlay) was cloned into isolated test directories.
- PostgreSQL 16 databases, native API, Django backend and Redis were disposable
  services on a private Docker network, with no production ports or databases.
- Native engine schema 001 through 009 applied successfully.
- Native API booted against schema 009 with independent limited runtime and
  platform principals.
- Real Native HTTP: regular customer domain and mailbox provisioned; generic
  Native credential denied for system staging; dedicated credential staged only
  inactive system domain and five inert mailbox rows; retry was idempotent.
- Real Django-to-Native HTTP with platform administrator auth, unauthorized
  tenant/anonymous refusal, audit records and readiness returned PASS, 1 test.
  This reproducible test is committed at
  `backend/tests/test_release1_native_bridge_isolated.py` and deliberately
  requires `MATEMAIL_ISOLATED_E2E=yes`, a disposable Native Engine, separate
  secrets, and the specific isolated host.
- Native Python authorization plus restricted runtime role tests: 14/14 passed.
- The previously deployed Native API image (minimum schema 6) successfully
  connected to disposable schema 9 and provisioned an ordinary customer
  domain. This is a software rollback compatibility observation, not
  a schema downgrade and not a complete production recovery drill.

## Broader Django regression

An initial incomplete test image could not run the whole repository suite
because it lacked pytest/git/frontend files. It is not a release gate result.

A second complete CI-parity environment included development Python packages,
Git, the whole source checkout, PostgreSQL and Redis. It executed the same
Django `manage.py test` command as GitHub CI:

- 2,187 tests run; 13 failures, 1 error, 5 skipped at earlier commit.
- All failures were mapped to stale test assumptions: 11/12 mock-only DKIM
  cleanup cases now need to stub the Phase 2 platform ownership lookup, a
  schema-6 hardcoded expectation became invalid with schema 9, and a newly
  added SQL helper was not in the explicit Git shell-script mode inventory.
- The error was a permission matrix fixture reusing an already reserved
  deleted domain name because it derived the name from the decreasing domain
  row count. Durable Phase 2 ownership reservations are intentional.
- Relevant tests were corrected without changing/deleting production guards.
  A new test verifies that tenant DKIM cleanup cannot delete a reserved
  platform domain.
- With fixes committed, **51/51 targeted cases PASSED**, including DKIM
  cleanup, permission matrix, schema contract and shell-script mode inventory.

These targeted tests DO NOT establish that the entire 2,187-test suite has
already passed on the final commit. A single pull request CI verification is
the remaining full-suite gate.

## Outstanding gates

- PR triggered GitHub CI and Native API security workflows must be green on
  this exact final commit. PR CI publishing is skipped and no deploy is
  implied by a passing result.
- Fresh Azure/Restic backup verification and documented operational rollback
  approval for the actual release window.
- Controlled release must keep system staging and sender/DNS cutover OFF.
- Real SMTP submission/inbound delivery, IMAP login, DKIM signing and PostBox
  smoke tests only occur after an approved deployment.

## Decision

**No merge or production deployment before green CI and explicit approval.**
The current Native Engine API process still holds an isolated bootstrap
migration credential; regular request handlers use a limited role when
`NATIVE_RUNTIME_DB_ENABLED=True`. That deployment switch needs its own
strong, unique secret and live readiness verification.
