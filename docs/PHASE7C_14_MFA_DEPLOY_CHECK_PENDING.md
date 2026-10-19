# Phase 7C-14 — MFA Deployment Configuration Gate (Pending Runtime Acceptance)

**Date:** 2026-10-19
**Branch:** `feature/phase7a-auth-transport-foundation`
**Status:** Source implemented and syntax/static review passed. **Runtime unit tests BLOCKED by safety checks; NOT an accepted release gate.**

## Problem

MFA release switches could be deployed independently with no Django deploy-time
validation of combinations or encryption key syntax. For example, enabling
PostBox MFA while Native SMTP/IMAP protection remains OFF could break PostBox
login or expose a mail-client bypass. A Fernet key that is absent, malformed,
or duplicated between Hub and PostBox can prevent recovery/continuity.

## Feature-branch change (no live activation)

Django's existing tenant AppConfig loads `apps.tenants.checks`. Added a
`Tags.security, deploy=True` registered check for these configuration
failures, with stable identifiers:

- `tenants.E030`: PostBox MFA and SMTP/IMAP security enabled separately.
- `tenants.E031`: MFA clients enabled without Native mail-engine adapter.
- `tenants.E032`: Require enforcement selected with policy disabled.
- `tenants.E033`: Invalid/missing PostBox Fernet key when mailbox security is ON.
- `tenants.E034`: Invalid/missing Hub Fernet key under Require enforcement.
- `tenants.E035`: Hub/PostBox share the same encryption key under Require.
- `tenants.E036`: Require activation blocked by independent
  `can_activate_require() == False` release guard.
- `tenants.E037`: Require selected without both mailbox MFA and SMTP/IMAP
  security switched ON.

All MFA flags OFF is an acceptable candidate without MFA keys, preserving
existing production default behavior. Policy-only Encourage can be configured
without enabling PostBox or SMTP/IMAP enforcement.

The check validates only key format and distinctness. It does **not** prove
that production's keys are persistent or backed up, the Native API can be
reached, sessions disconnect, or an operator approved deployment.

## Added tests and verification limits

Added ten Django `SimpleTestCase` checks using **synthetic-only** Fernet keys
and `override_settings`. They cover OFF defaults, policy Encourage, invalid
flag combinations, missing/invalid keys, wrong adapter, distinct keys, and
the Require release lock.

**Important:** Attempting the isolated test runner was rejected by tool
safety checks. The rejected test was **not retried or rerouted**. Only
Python syntax compilation and `git diff --check` were completed. No
passing Django unit test or green remote GitHub Actions CI claim is made
for this change. These cases must be executed through a subsequently
approved normal CI workflow before this source can be released.

## Remaining full release gates

- Approved and passing PR-targeted GitHub CI, including these ten tests,
  backend full suite, Native candidate suite and frontend checks.
- Production secret storage/key persistence, backups and restore, image
  digests, rollout/rollback rehearsal and owner-approved canary.
- Organization Require MFA remains deliberately activation-locked; no flags
  are being changed, no production mail clients or databases touched.
- Preserve all TestLab artifacts until Phase 8 cleanup.
