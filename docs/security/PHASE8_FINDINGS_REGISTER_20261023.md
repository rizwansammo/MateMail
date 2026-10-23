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

## Follow-up reconciliation (2026-10-23)
- **P8-001:** Current Native Postfix/Dovecot/Rspamd/ClamAV immutable image identifiers were reconfirmed running/healthy. `trivy` and `grype` were not installed on MateServer. Existing GitHub security jobs use `pip-audit` for candidate Python dependencies and `npm audit --omit=dev` for production JS, but these are not full operating-system package scans of all Native images. Image CVE status remains OPEN.
- **P8-002:** Live `mateerp-database-1` later existed and was healthy. The Azure DR backup failure was transient lack of the referenced container at the time of the run; no incorrect container-name claim or script fix was made. Local Restic repository check passed 15/15 snapshots; this does not substitute for Azure DR.
- **P8-005:** GitHub Advisory `GHSA-vfj7-8cjw-p6xm` still displayed `braces <=3.0.3` with no patched version on 2026-10-23: https://github.com/advisories/GHSA-vfj7-8cjw-p6xm . A bounded frontend runtime directory search produced no match. Keep the development toolchain issue OPEN until a trusted mitigation/fix is reviewed.
- **P8-010:** Four dirty-worktree paths did not byte-match release. 14 affected working-file paths plus tracked diff archived root-only, with original worktree intact. Review differences before deletion.

## Public repository and stale worktree findings
- The GitHub repository API reported `visibility=public` as of the review. This is not by itself a leaked secret, but makes verification of historical secret-scanning alerts and any credential rotation particularly important. Connector access did not expose the necessary secret scanning endpoints, so remediation remains **Not Verified**.
- Inspection of the four differing files showed that the final release contains additional security functionality absent from the older UI worktree, including Hub MFA recovery routes and PostBox backup-code regeneration/UI. Therefore the older worktree must never be blindly promoted over the production release. A full provenance/reconciliation review is still needed before discarding or cherry-picking its contents.

## P8-002 live backup resolution note (2026-10-23)
The latest Azure DR scheduled run returned success on 2026-10-23. Both actual Azure blobs, their remote checksum files, the full tar/gzip archive, all 2,786 embedded content hashes, 11 DB custom archive TOCs and required DR categories were verified with no restore. **The previously failed scheduled job's immediate backup-availability impact is Resolved**. Leave a separate **Deferred monitoring/resilience follow-up** to prevent recurrence when a dependent application database container is temporarily unavailable. Do not mark full VM failover as tested.

## Live Phase 8C review, 2026-10-23
Full scoped **13 running image OS** read-only scan completed, using official checksum-verified Trivy 0.75.0 and exact Docker image IDs, with never-started ephemeral rootfs exports. See `PHASE8_C_CURRENT_IMAGE_SECURITY_ASSESSMENT_20261023.md`. **9 Critical records** remain in Dovecot (8 across four MariaDB CVEs, two packages) and Rspamd (one libxml2 CVE). High records also remain in other images. Scanner results indicate review urgency, not proven remote exploitation. Critical remediation / independent mitigation approval is unresolved.

**P8-003 now has concrete private evidence:** two GitGuardian notifications on the public MateMail repository were confirmed via connected Gmail (Generic Password, Oct 8 push; Generic Database Assignment, Oct 10 push). The incidents may be genuine secrets or false positives; credential validity and revocation/rotation status are **Not Verified**. Treat as urgent and triage privately. Root-only remediation checklist: `/root/matemail-phase8-evidence/gitguardian-private-triage-20261023.md`. Never publish values/incident URLs.

**P8-009 monitoring:** `matemail-prometheus` is currently stopped/unhealthy; last exit (code 0) 2026-10-18, with orderly shutdown logging and `unless-stopped` policy. Grafana and Alertmanager remained healthy. Security alerting effectiveness remains **Not Verified**, and no restart was made without owner approval.

## 2026-10-23 Monitoring remediation
After verifying the service was an existing `matemail-monitoring` Compose container, its stop state, resource limit and network, the previously stopped `matemail-prometheus` container was selectively started. Prometheus subsequently became **Running/Healthy**, returned `Prometheus Server is Healthy` on the health endpoint, and its targets API reported **3 of 3 active scrape targets UP**. Five sampled core mail containers stayed Running/Healthy. Grafana/Alertmanager were not restarted. Mark **P8-009 current service outage Resolved**, with normal alert-delivery acceptance distinct and not newly tested.

**P8-001 and P8-003 remain open:** Debian stable security tracker lists vulnerable MariaDB and libxml2 packages; this review must not import unstable/test packages into pinned production images without safety tests. GitGuardian incident state/rotation cannot be confirmed using currently exposed GitHub connector permissions.

## 2026-10-23 Dependency hotfix merged; deployment pending
The documentation PR uncovered newer upstream advisories not covered at the original 2026-10-21 release: `next@16.3.8` affected by multiple Next.js 16.4.0-and-earlier advisories (including unauthenticated RCE), and `djangorestframework==3.17.2` affected by three CVEs with fix in 3.18.2.

**Remediation merged to main:** [PR #154](https://github.com/rizwansammo/MateMail/pull/154), commit `4942c4d6b9044127b6611418bf45b20480e80f7b`, updated Next.js and eslint-config-next to **16.4.1**, DRF to **3.18.2**, and regenerated npm lock. Patch-branch CI and Frontend production smoke passed; production npm audit reported zero vulnerabilities.

**CRITICAL OPERATIONAL DISTINCTION:** GitHub merge does NOT deploy. The currently running production app may still be on old SHA `1638299f...` until the owner performs authorized manual deployment of newly published immutable images. Treat those application vulnerabilities as **code fixed, production patch NOT YET VERIFIED** until deployment, smoke, migration and rollback checks. Do not trigger deployment implicitly.
