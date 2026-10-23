# Phase 8C: Current Immutable Image Security Assessment

Date: 2026-10-23, Asia/Dhaka

**Outcome: Image inventory and scoped operating-system vulnerability assessment completed. Remediation / risk approval is outstanding. Do NOT mark Phase 8C closed.**

## Method
- Obtained official Aqua Trivy `v0.75.0` release and verified upstream SHA256 release checksum.
- Updated vulnerability database on the audit host, then scanned filesystem exports from **never-started, disposable containers** constructed from the **actual running image IDs**. Resource-sparing sequential scans used `nice`, `ionice` and one worker; no production container/image was changed.
- Original layer-based Trivy image read failed for Dovecot due to a Docker archive blob error; rootfs scanning avoids claiming nonexistent successful layer provenance.
- `--scanners vuln --pkg-types os --severity HIGH,CRITICAL`; this is **OS packages only**. GitHub CI separately covers app Python/production npm dependencies. Counts are advisory/package records, not unique exploitable vulnerabilities.
- Raw scanner JSON and logs are root-only at `/root/matemail-phase8-evidence/scan-20261023/`. No raw logs, potential customer secrets or credentials published in GitHub.

## Running image scans
| Running image | Critical records | High records | High records with scanner fix | Review |
| --- | ---: | ---: | ---: | --- |
| Native Dovecot | **8** | 67 | 11 | Critical Open |
| Native Rspamd | **1** | 50 | 0 | Critical Open |
| Native Postfix | 0 | 69 | 5 | High Open |
| Native PostgreSQL (exact running digest, NOT local mutable tag) | 0 | 9 | 9 | Fixable High |
| Native Redis | 0 | 4 | 4 | Fixable High |
| Native API | 0 | 0 | 0 | No findings in scoped OS scan |
| Native Olefy | 0 | 0 | 0 | No findings in scoped OS scan |
| Native Policy runtime | 0 | 7 | 7 | Fixable High |
| Native Submission Gateway | 0 | 4 | 4 | Fixable High |
| App Backend | 0 | 48 | 0 | High, reachability review needed |
| App Frontend | 0 | 0 | 0 | No findings in scoped OS scan |
| Native Unbound | 0 | 10 | 10 | Fixable High |
| Native ClamAV | 0 | 1 | 1 | Fixable High |

**Note:** A test of the mutable local `postgres:16-alpine` tag erroneously returned 0/0 but its image ID did not match the running digest. It was correctly excluded and replaced with a scan against `sha256:cf78e76683b9ca8c57...`.

## Critical records triage
- Native Dovecot: eight package/advisory records represent **four distinct MariaDB CVEs**, repeated for `libmariadb3` and `mariadb-common`: `CVE-2026-44172`, `CVE-2026-49261`, `CVE-2026-85746`, `CVE-2026-92262`.
- Installed package `1:11.8.6-0+deb13u1`; Trivy reports no fixed package version in the running Debian release. Debian security tracker lists Trixie as vulnerable and newer upstream/forky builds as fixed for several. Do NOT mix testing/unstable packages into an official Dovecot image without compatibility review.
- Actual Dovecot effective config uses PostgreSQL and no MariaDB SQL configuration, which reduces relevance of vulnerabilities requiring MariaDB server operations. However **one of eight sampled Dovecot processes mapped `libmariadb`**, so it is wrong to assert the library is absent or completely unreachable. Each CVE's vulnerable call path still needs vendor-specific proof.
- Native Rspamd: `CVE-2026-6653` in `libxml2`; Trivy reports one Critical record with no fixed version. Debian records the current stable package as affected and newer branches as fixed. Malicious XML reachability in Rspamd was **not** proven or ruled out.
- Avoid claiming any current exploitation, accepting a critical scanner score as automatic exploitability or claiming zero risk.

## Development-only advisory
GitHub issue #74: `braces <=3.0.3`, `CVE-2026-93687` / `GHSA-vfj7-8cjw-p6xm`. GitHub advisory currently lists **no patched version**. Prior Next ESLint chain is development-only; a bounded search of running standalone Frontend runtime found no `braces` directory, but this does not certify every possible runtime path. Track upstream fix; do not weaken lint or suppress the advisory to fake completion.

## Other open findings and approved scope
- GitHub issue #76: retains High/Critical Native image remediation; **cannot close with new critical results**.
- GitHub issues #79 (DMARC reporting) and #80 (MTA-STS/TLS-RPT) remain separately gated deliverability work. Operator intentionally keeps DMARC `p=none`; no DNS enforcement change was made in this Phase.
- Potential repository secret exposure must be treated as an independent credential incident, not replaced by CI passing. Private triage details belong in a restricted report, not this public repo.
- Production still healthy at last checked read-only inspection, with no service restart, package install, image replacement, maildata modification or migration.

## Safe remediation acceptance gates
1. Track pinned base image updates/verified upstream patches, or a separately reviewed minimal compatible derived image with security packages adjusted. If a library is removed, prove Postfix/Dovecot auth and delivery plus Rspamd processing compatibility before proceeding.
2. Scan exact new candidate digest including OS packages, verify original CVEs are gone or have documented verified mitigation, preserve image digest and provenance.
3. Tests: schema 016 compatibility, Native/IMAP/SMTP protocol policy, tenant isolation, revocation/recovery, spam/ClamAV attachment handling, prior credentials and DKIM continuity.
4. Backup/rollback readiness before any controlled manual rollout; never delete mail/DB volumes or bypass credentials.
5. Require documented owner acceptance for any remaining Critical/High security risk.

**C state: all 13 scoped OS images assessed; High/Critical remediation and secret validity/rotation evidence remain OPEN.**

## Additional detection and monitoring findings (restricted details kept private)
Two GitGuardian email notifications for `rizwansammo/MateMail` were independently located: a Generic Password alert associated with an October 8, 2026 push and a Generic Database Assignment alert associated with an October 10, 2026 push. These are **alerts, not proof of two live usable credentials**. No credential validity, rotation, revocation or false-positive classification has been verified. Because the repository is public, treat both as **urgent unverified credential-exposure incidents**; do not declare them remediated. Full triage instructions and timestamps are in the root-only file `/root/matemail-phase8-evidence/gitguardian-private-triage-20261023.md`; **do not copy secret values, incident URLs or sensitive details into this public repository**.

The production monitoring stack also has `matemail-prometheus` **Exited (0), unhealthy**, stopped since 2026-10-18. Docker logs indicate orderly shutdown, not an explained crash; the restart policy is `unless-stopped`. Grafana and Alertmanager are healthy, but without Prometheus actively scraping/forwarding, telemetry and alert coverage may be incomplete. **Cause / operator intent not verified**, and no restart was attempted. Keep the monitoring coverage issue open for a separately approved targeted check.

## Corrected scope and closure decision
The current Trivy image review is **done**, but Phase 8C has **unresolved critical findings** in Dovecot and Rspamd, unresolved GitGuardian notifications and monitoring visibility uncertainty. Do not mark Phase 8C accepted or complete merely because the findings have been catalogued.
