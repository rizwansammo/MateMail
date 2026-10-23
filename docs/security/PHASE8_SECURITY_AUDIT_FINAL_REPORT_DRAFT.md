# MateMail Security Audit: Phase 8 Final Report (Draft)
**Review date:** 2026-10-23 (Asia/Dhaka)
**Status:** IN PROGRESS. NOT ACCEPTED FOR FINAL CLOSEOUT.
**Baseline:** PR #152, commit `1638299f6046e8493d74e64077ef7599b45ba6d7`.

## Executive summary
Security release implementation for Phase 7A, 7B and 7C was previously completed. Phase 8 has independently reconfirmed the production image references, service health, Django deployment check, migration status, Native schema v16, MFA flags, encryption-key validity and current tenant policies without changing production.

**Phase 8 cannot be declared closed:** open high/critical image advisory follow-up #76 has not been reconciled against current images; a scheduled Azure DR backup failed on 2026-10-22; current GitHub secret scanning / rotation evidence was not accessible; independent full-stack recovery and owner-approved real-client MFA acceptance remain unverified.

## Verified on MateServer
- Main, backend and frontend reference commit `1638299f6046e8493d74e64077ef7599b45ba6d7`.
- Native API: `ghcr.io/rizwansammo/matemail-native-api@sha256:8abddc37572a2d42b706ee859722fc21a66c7795281f6b122077efb100a9d65e`.
- Backend, frontend, Celery worker and beat, Native API, Postfix, Dovecot, PostgreSQL, Redis, Rspamd, ClamAV and Olefy observed healthy.
- Native `schema_version=16` and `016_native_tenant_mfa_enforcement`. Django unapplied migrations: zero. Django `check --deploy`: zero issues.
- Running app flags: Native adapter, PostBox 2FA ON, SMTP/IMAP security ON, org-policy/enforcement readiness ON; PostBox canary OFF.
- `can_activate_require()` returned True. All four live organizations retained `off` policy, with no automatic Required enrollment.
- Hub and PostBox Fernet keys are syntactically valid and distinct. No key values were emitted.
- PostBox homepage and Hub Security endpoint both returned HTTP 200.

## Source-bound acceptance distinctions
The isolated protocol tests, native recovery tests and prior CI runs support previous implementation acceptance. The saved synthetic test logs are **not** proof of current real-customer end-to-end Require MFA acceptance. Previously recorded handoff counts of 16/16 Native unit and HTTP tests and nine PostgreSQL assertions remain historical claims pending source-artifact reconciliation.

## Open gates
See `PHASE8_FINDINGS_REGISTER_20261023.md`, `PHASE8_BACKUP_RECOVERY_20261023.md` and `PHASE8_TESTLAB_CLEANUP_20261023.md`.
No production service was restarted, no policies were changed, and no production resources were deleted. Two separately protected root-only evidence archives were created and verified. Scope-validated TestLab cleanup removed eight exited test containers, two isolated networks and three unshared anonymous test volumes; all ten immediately checked production containers remained healthy. Filesystem and worktree cleanup remains pending.

## Evidence references
- CI: https://github.com/rizwansammo/MateMail/actions/runs/39602267857
- Native publish: https://github.com/rizwansammo/MateMail/actions/runs/39602268088
- Native security: https://github.com/rizwansammo/MateMail/actions/runs/39602268498
- Isolated SMTP/IMAP protocol acceptance (prior PR SHA): https://github.com/rizwansammo/MateMail/actions/runs/39601535754
- Source: `docs/PHASE7A_ORG_2FA_SMTP_IMAP.md`, `docs/PHASE7B_HUB_POSTBOX_SECURITY_UI.md` and Phase 7C reports.

## Phase 8 continuation evidence, 2026-10-23
- Mailbox transport TLS: IMAPS 993 and SMTP STARTTLS 587 negotiated TLS 1.3 with verified certificates; no client login or mail delivery was tested.
- Local Restic: 15 snapshots, repository check returned `no errors were found`. Azure offsite job is still not verified following its scheduled failure.
- Scoped lab Docker cleanup: eight exited containers, two empty isolated networks, three anonymous volumes were removed after restricted inspection/log preservation; post-cleanup 10/10 key production containers remained running and healthy.
- The clean detached release test worktree was removed using normal Git operations; the older source branch and dirty UI worktree were retained. Four files in the dirty worktree differ from release, so do not discard them until reviewed. A root-protected archive of all 14 affected working files and their tracked diff was verified.
- Current advisories: GHSA-vfj7-8cjw-p6xm remained without upstream patched version, and the Native image OS CVE issue remains open. Candidate Python `pip-audit` and production JS `npm audit` do not replace comprehensive OS image scan.
- No live production mail reconfiguration, restart or customer policy change.

## Residual access and branch controls
The repository currently reports public visibility, a known owner-managed workflow choice rather than proof of credential compromise. A previously mentioned possible secret exposure cannot be confirmed remediated using the available GitHub connector access. Before release closeout, authorized owners must inspect GitHub secret scanning alerts and rotation/revocation evidence. Documentation remains on a non-main branch; the normal CI pipeline triggers on all pull requests, so a premature documentation PR was avoided.

## Azure DR recovery readiness, current verification (2026-10-23)
The scheduled 2026-10-23 whole-server Azure DR backup completed successfully. Verified **both actual Azure objects** `latest/mateserver-latest.tar.gz` and `daily/2026-10-23/mateserver-full.tar.gz` at 66,762,044 bytes with two independently checked matching remote SHA256 companion objects. Full archive read and 2,786 internal file/link SHA256 checks passed, zero mismatches. All 11 PostgreSQL custom-format backup TOCs were readable without restoring. Required MateMail and host configuration/data categories, recovery runbook and shell-syntax-checked restoration scripts were present.

This resolves the **current backup-upload/integrity verification** gate following the 2026-10-22 scheduled failure; it **does not** represent a new live restore or blank-VM failover acceptance. The owner explicitly prohibited any restoration or repeat restore rehearsal. Other Phase 8 gates, including CVE and secret exposure verification, remain open.

## Important unresolved B/C acceptance as of 2026-10-23
An exact-image Trivy OS scan found 8 Critical records in Dovecot and 1 in Rspamd; source evidence and package/provider status are recorded in the new Phase 8C report. Native API, Olefy and Frontend had no High/Critical findings **in this scoped OS scan**, but several other images have High advisories. Two prior GitGuardian notifications for public GitHub code were located, without proof of false-positive classification or credential revocation/rotation. Prometheus is stopped/unhealthy, despite healthy Grafana and Alertmanager.

Phase 8B release CI and canary safety-code review completed, but real owner-controlled browser-to-protocol and mail-client acceptance remains outstanding. Do NOT mislabel B or C Closed, and do NOT proceed with final Phase 8 sign-off without defensible evidence, mitigation and authorization.

## Phase 8 workstream state reconciliation
- A Production verification: Verified.
- B Security acceptance: release CI and separate protocol evidence verified, **full production browser-to-protocol and real mail client end-to-end acceptance not verified**.
- C Security findings: scanning/triage complete, **image CVEs and two possible secret leaks not remediated/verified**; **Prometheus service outage resolved** with 3/3 targets healthy.
- D Stage 7 TestLab cleanup: **Completed**, with Git/source/browser/modified files preserved securely before removing only the TestLab directory.
- E Documentation: evidence drafted and ready for review; **official security sign-off not granted**.
- Azure backup: latest and date-pinned blob SHA256 and full internal manifest verified; no restoration attempted under owner prohibition.

**No automatic assertion of complete Phase 8 acceptance is permitted while unresolved Critical findings and credential incidents remain.**

## Urgent newly identified framework dependency advisories
At the documentation PR CI stage, audits failed for production `next@16.3.8` and `djangorestframework==3.17.2`. The exact corrective versions Next.js **16.4.1** and DRF **3.18.2** were updated and validated in [PR #154](https://github.com/rizwansammo/MateMail/pull/154) (successful PR CI and frontend runtime smoke), and merged to main at `4942c4d6b9044127b6611418bf45b20480e80f7b`.

This resolves these specific vulnerabilities **in repository source and build dependency definitions**. It does not constitute live server remediation: the manual deployment workflow was not triggered as part of Phase 8, and production still requires digest verification after human-controlled rollout. Dovecot/Rspamd OS vulnerabilities and GitGuardian exposure status remain separate unresolved gates.

## Additional independent DNS acceptance blocker
Final production service health was green and direct loopback Host/SNI HTTPS returned HTTP 200, but public Google and Cloudflare DNS resolvers returned `SERVFAIL` for the MateMail apex, Hub and PostBox hosts. The authoritative Spaceship nameservers themselves returned DNS response code 2. **Full external production availability is not Verified.** This is a registrar/provider DNS zone investigation separate from Docker/TLS service health. No DNS/DS/NS changes were performed.
