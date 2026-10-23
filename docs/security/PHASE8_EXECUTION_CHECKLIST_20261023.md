# MateMail Phase 8 Final Execution Checklist
**Updated:** 2026-10-23 (Asia/Dhaka)
**State:** Workstream A complete for sampled read-only checks; Workstreams B–E require follow-up.

| Item | Current state | Next action |
| --- | --- | --- |
| A1 GitHub main/PR #152 release | Verified | Recheck if main changes |
| A2 Backend/frontend and Native digest provenance | Verified | Preserve current references |
| A3 Docker mail-stack health | Verified, point in time | Repeat after cleanup |
| A4 Django/Native migrations | Verified | No migration needed |
| A5 Runtime flags and Fernet keys | Verified | Preserve key continuity |
| A6 Organization policy integrity | Verified, four Off | Do not change customer policies |
| A7 HTTP routes | Verified | Repeat at closing |
| B1 Historical acceptance logs | Reviewed subset | Correlate with exact release and source artifacts |
| B2 Real end-to-end client/owner Require flow | Not Verified | Approved isolated or customer-safe manual acceptance |
| C1 Container OS/image advisories (#76) | Open | Fresh targeted scan and reviewed risk disposition |
| C2 Repository secret-scanning/rotation state | Not Verified | Authorized GitHub security assessment |
| C3 Backup availability | Open: 2026-10-22 Azure job failed | Verify Azure and next success or approved controlled retry |
| C4 Restore/rollback assurance | Partial historical evidence | Controlled isolated restore, no production restore |
| C5 Other issues #74, #79, #80, #146 | Open / Deferred | Evidence-based classification |
| D1 TestLab file and Docker inventory | Completed initial read-only inventory | Check remaining anonymous volumes and owners |
| D2 Evidence preservation | Initial protected archive verified | Preserve archive, assess browser fixtures and offsite copy before deletion |
| D3 Worktree safety | Blocked: eleven uncommitted ui-qr-setup paths | Reconcile and preserve changes before removal |
| D4 Temporary resource cleanup | Partial: eight stopped containers, two isolated networks and three test-only volumes removed | Assess filesystem, browser artifacts and worktrees separately |
| D5 Health regression after cleanup | Ten production containers verified running/healthy immediately after Docker cleanup | Broader SMTP/IMAP and filesystem cleanup regression pending |
| E1 Seven Phase 8 Markdown reports | Drafted on documentation branch | Review findings and revise with final evidence |
| E2 Final PR/CI/merge | Pending | Avoid premature CI expenditure |
| E3 Formal acceptance | Blocked | Close only with defensible evidence and owner approval |

**Safety:** no live policy flips, no customer test mailbox, no image rebuild, no restart, no production configuration change and no resource deletion without the defined safety gates.

## Update, 2026-10-23
- Completed: TestLab Docker cleanup of eight test containers, two isolated networks and three unshared anonymous volumes.
- Completed: clean detached GA worktree unregister/remove; original branch and dirty UI worktree preserved.
- Completed: root-only archives of TestLab results, Docker metadata/logs and UI worktree modified/untracked content, with checksums.
- Completed: IMAPS and SMTP Submission STARTTLS 1.3 handshake and certificate verification, without authentication or mail transmission.
- Completed: local Restic snapshot count (15), latest dated 2026-10-22, `restic --no-lock check` passing.
- Remaining: offsite Azure DR revalidation after 2026-10-22 failure, current image OS/package vulnerability review, security/secret scan alert evidence, full approved MFA E2E acceptance, analysis of four differing UI-worktree files, filesystem/browser QA cleanup and final acceptance.

## Merge gate and CI cost
GitHub `.github/workflows/ci.yml` runs on every pull request into main, including documentation-only requests. For this reason the draft Phase 8 documentation remains on `docs/phase8-security-evidence-20261023` without a premature PR/CI run. Review and consolidate outstanding evidence before opening the final documentation PR. Never merge an outdated local MFA implementation on top of the release.

## Live Azure DR verification update: 2026-10-23
- **Completed / Verified:** Scheduled Azure DR backup successful (2026-10-23, 03:33:41 CEST); Azure latest and dated full backup both HTTP 200 and 66,762,044 bytes; full remote SHA256 against both checksum companion files; both blob hashes identical; local archive digest equal.
- **Completed / Verified:** Read complete gzip/tar archive and independently compare all 2,786 embedded content hashes, including hardlinks; zero mismatches. All eleven DB dumps passed `pg_restore -l` TOC checks. MateMail App, Native Engine, SMTP/IMAP data, DKIM/TLS, host configurations, and recovery runbook/scripts are present.
- **Deliberately not in scope:** Any database/file restore on MateServer, repeat isolated restore, new VM, production service action or backup job rerun. Prior successful isolated restore evidence remains valid historical context.
- **Residual operational consideration:** The 2026-10-22 scheduled backup failure remains a resilience incident even though the 2026-10-23 backup succeeded. Monitor for recurrence; do not silently change shared backup script.

## B/C status gate after investigation, 2026-10-23
- **B:** exact release CI, Native isolated tests, runtime checks and updated canary code reconciled. **Actual single-identity Browser/TOTP → Native → IMAP/SMTP and real-client/owner Require acceptance are Not Verified.** Do not bypass previously blocked synthetic E2E command with an alternative.
- **C:** all 13 current running image OS rootfs scans completed and stored root-only. **Critical open**: Dovecot MariaDB advisories, Rspamd libxml2; selected High findings, two GitGuardian credential notifications without rotation/revocation proof, and stopped Prometheus monitoring.
- B and C are **investigated/documented, not fully accepted or closed**. Their end gates require safe remediation and owner-approved acceptance where necessary, not merely a table status update.

## Post-review 2026-10-23 final workstream ledger
- **D = COMPLETE:** safe full `/opt/TestLab/MateMail-Stage7A` cleanup performed. Two Git refs, all 14 modified/untracked UI files and browser/test source artifacts protected in verified root-only archives/bundle.
- **C monitoring item = RESOLVED:** `matemail-prometheus` started, healthy, all 3 of 3 targets up, core sampled mail containers still healthy.
- **C CVE item = OPEN:** Debian MariaDB/libxml2 vendor fixes are not available for all deployed pinned stable runtime packages; no unsafe cross-release package mixing and no unreviewed mail image deployment.
- **C possible secret exposure item = OPEN:** two GitGuardian reports exist; still requires verified false-positive finding or credential revocation/rotation.
- **B real-client acceptance = NOT VERIFIED:** source and CI reconciliation complete; forbidden to claim a synthetic mock-chain is real production end-to-end proof.
- **E documentation = DRAFT/REVIEW READY:** reports reflect verified state; formal release/security closeout cannot truthfully be signed until B/C gates are resolved or explicitly accepted under policy.

## Framework security patch workflow
- New Critical Next.js and Django REST Framework advisories detected by PR #153 CI (audits intentionally failed closed).
- Separate dependency-only PR #154 updated `next@16.4.1`, matching eslint config, and `djangorestframework==3.18.2`; lockfile regenerated.
- Both patch PR GitHub CI and Frontend production runtime security smoke passed; PR #154 merged as `4942c4d6b9044127b6611418bf45b20480e80f7b`.
- **Outstanding:** main-branch image publication confirmation, manual production deploy, actual running image SHA/digest and postdeployment regression. Never claim code merge already fixed the live app.

## DNS final availability gate
**OPEN:** read-only query to both authoritative `launch*.spaceship.net` name servers returned DNS SERVFAIL. Hub and PostBox loopback requests succeeded but public host resolution failed. Verify registrar zone provisioning/authoritative DNS and external end-to-end access, preserving DNSSEC, DKIM and customer MX records. This needs authorized registrar access; no automatic DNS edits.
