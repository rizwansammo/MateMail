# Phase 8B: Security Acceptance and Regression Reconciliation

Date: 2026-10-23, Asia/Dhaka

**Outcome: Evidence review completed. The full production end-to-end acceptance gate is NOT VERIFIED, so Phase 8B cannot be marked fully accepted.** No customer mailboxes, passwords, 2FA settings, policies, traffic, databases or services were modified.

## Baseline and exact release
- GitHub main commit: `1638299f6046e8493d74e64077ef7599b45ba6d7` (PR #152).
- Running Backend, Frontend and Celery images carry the expected application SHA.
- Running Native API digest: `8abddc37572a2d42b706ee859722fc21a66c7795281f6b122077efb100a9d65e`.
- Native schema version 16 (`016_native_tenant_mfa_enforcement`), no unapplied Django migrations, `manage.py check --deploy` reported no issues.
- App security switches ON with `POSTBOX_2FA_CANARY_ENABLED=False`; Fernet keys validated distinct; `can_activate_require()` TRUE. At verification time, **all four organizations were OFF**, none silently enforced Require.
- Production IMAPS port 993 and SMTP STARTTLS port 587 completed verified TLS 1.3 handshakes. A transient first SMTP connection timed out; verified retry passed without logging in or sending mail.

## GitHub acceptance on actual merged SHA
- [CI run 39602267857](https://github.com/rizwansammo/MateMail/actions/runs/39602267857): Backend tests, PostgreSQL tenant MFA acceptance, app secret-key continuity, Django migrations, package audits, Frontend tests/build and GHCR image publication successful.
- [Native publish run 39602268088](https://github.com/rizwansammo/MateMail/actions/runs/39602268088): immutable Native API candidate build/publish successful.
- [Native security run 39602268498](https://github.com/rizwansammo/MateMail/actions/runs/39602268498): Native candidate unit security and Python audit successful.
- [Isolated real IMAPS/SMTP run 39601535754](https://github.com/rizwansammo/MateMail/actions/runs/39601535754): accepted four scenarios for prior PR SHA `983cda0756f1967cbd46aa6ffd221e69a7bd96b2`, not the exact final merge SHA.
- Prior TestLab evidence: Native issued credential accepted through real Dovecot/Postfix; required dedicated-client policy rejects legacy passwords; unrelated mailbox unaffected; live IMAP revoke terminates target session; recovery after temporary Native control-plane failure; native protocol policy matrix and tenant isolation. Read-only archived logs remain root-protected on MateServer.

## Issue #146 older blocker reconciliation
The 2026-10-20 issue correctly described a previous global-flag failure, but the merged source now contains:
- `apps.postbox.canary_scope.native_primary_required_for_mailbox` and UUID-scoped canary rules.
- `trusted_primary_authenticate` preserves legacy IMAP verification for **unenrolled, noncanary** accounts; enrolled, required or canary accounts use Native.
- `test_postbox_mfa_canary_isolation.py` exercises noncanary login and denies cross-scope enrollment, Native verifier fallback, rollback downgrade and scoped challenge. This is mocked transport acceptance, not real production end-to-end coverage.
That obsolete code blocker is **resolved in source**, while the issue's real-browser/protocol and real-client conditions remain unproven. Do not close issue #146 solely on the source correction.

## Acceptance matrix
| Gate | State | Basis |
| --- | --- | --- |
| CI, runtime release and schema provenance | Verified | Exact SHA/digests and build tests |
| 2FA key continuity, isolated scope, tenant boundaries | Verified by CI / isolated evidence | Exact release and synthetic tests |
| Native Dovecot/Postfix credential policy and immediate revocation | Verified historically in isolated real-protocol tests | Previously retained logs and CI |
| Public 993 / 587 TLS negotiations | Verified, unauthenticated | Live TLS 1.3 certificate checks |
| Owner controlled Organization Require API readiness | Verified as configuration | Function true; four tenants Off |
| Actual tenant-owner Require save with full Native policy reconciliation | **Not Verified** | No live policy mutation authorized |
| Real web login, TOTP enrollment and Native issue -> IMAP/SMTP on SAME synthetic identity | **Not Verified** | Previous blocked runner MUST NOT be bypassed |
| Gmail / Outlook / Thunderbird / Connected App actual sign-in, send, receive and revoke | **Not Verified** | No safe authorized test accounts/credentials |
| New isolated full restore | Explicitly out of scope | Owner prohibition and prior historical evidence |

## Closeout rule
Do not use synthetic/mock CI and separately passed real-protocol tests to claim a single true end-to-end production acceptance. No previous execution-safety control may be bypassed with an alternate script/tool. Real-client acceptance requires operator-controlled authorized test accounts, original app UI and expected protocol flow without risking customer services. Otherwise retain it as an explicit deferred/owner-approval release gate, not Passed.

**B state: source and evidence reconciliation completed; full security acceptance remains blocked on specified real end-to-end proof.**
