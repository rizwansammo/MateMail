# Phase 8 Security Findings and Remediation Register
**Date:** 2026-10-23. **Status:** OPEN ITEMS, no final closure authorization.

| ID | Severity/priority | Finding | Current disposition | Evidence / next action |
| --- | --- | --- | --- | --- |
| P8-001 | High until assessed | Historical Native OS/image High/Critical advisories | Open | GitHub issue #76, 2026-10-09 Trivy baseline. Compare exact current images with fresh, scoped analysis; record mitigation or safe image update. Never infer exploitability from count alone. |
| P8-002 | High, recovery availability | Azure DR scheduled run 2026-10-22 failed | Open | Service exit 1. Backup log: requested `mateerp-database-1` was absent at backup time. Container now present/healthy. Confirm current Azure snapshots and next successful run or approved controlled retry. |
| P8-003 | Unknown pending verification | Previously mentioned possible repository secret exposure | Not Verified | Requires authorized GitHub secret scanning alert details and concrete revocation/rotation evidence; do not publish credentials. |
| P8-004 | Acceptance gate | Full user browser → Native → IMAP/SMTP and real-client Require workflow | Not Verified | Issue #146 contains older gap. Compare against PR #152 evidence, then owner-approved isolated or representative client test. |
| P8-005 | Development/CI risk | ESLint `braces` advisory, historical unpatched chain | Open | Issue #74; check current dependency tree and upstream fix. Document development-only reachability. |
| P8-006 | Deliverability follow-up | DMARC aggregate reporting / staged enforcement | Deferred pending per-domain plan | Issue #79. No unauthorized DNS policy changes. |
| P8-007 | TLS hardening follow-up | Inbound MTA-STS / TLS-RPT rollout | Deferred pending verified HTTPS policy, DNS and MX | Issue #80. No unsafe premature enforcement. |
| P8-008 | Recovery acceptance | Full-stack independent current restore/failover | Not Verified | Earlier 2026-10-09 Azure isolated DB restore is documented; current full-stack failover and 2026-10-22 archive are not verified. |
| P8-009 | Monitoring follow-up | Prometheus container observed Exited (0) | Not Verified | Determine monitoring service design/ownership before classifying as incident. |
| P8-010 | Cleanup safety | TestLab includes uncommitted `ui-qr-setup` worktree files | Open preservation gate | Reconcile changes before any worktree deletion; no silent loss of source. |

## Resolution rule
Use `Resolved`, `Accepted Risk`, `Deferred` or `Open` only with dated supporting evidence. `Not Verified` in this document is an acceptance state, not an assertion of vulnerability. No critical/high finding can be silently accepted as resolved.

## References
- https://github.com/rizwansammo/MateMail/issues/76
- https://github.com/rizwansammo/MateMail/issues/74
- https://github.com/rizwansammo/MateMail/issues/146
- https://github.com/rizwansammo/MateMail/issues/79
- https://github.com/rizwansammo/MateMail/issues/80
- `docs/NATIVE_ENGINE_IMAGE_AUDIT_20261009.md`
