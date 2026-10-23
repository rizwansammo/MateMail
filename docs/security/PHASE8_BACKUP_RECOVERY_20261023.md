# Phase 8 Backup, Recovery and Rollback Readiness
**Date:** 2026-10-23. **Status:** NOT ACCEPTED FOR FINAL DR CLOSEOUT.

## Existing backup architecture
See `docs/BACKUP_RESTORE.md`: MateMail local encrypted Restic repository and an independent, scheduled whole-MateServer Azure DR system. Local Restic `OFFSITE_REPOSITORY` is not configured; do not mislabel local backup as offsite. Historical 2026-10-09 Azure download, checksum validation and isolated PostgreSQL restore succeeded. A complete blank-VPS failover was explicitly deferred.

## New live evidence
- `matemail-backup.service`: successful scheduled result at 2026-10-22 03:32 CEST.
- `mateserver-backup.service`: failed at 2026-10-22 03:32 CEST, exit 1.
- Latest failed DR log reports `error: no such object: mateerp-database-1`.
- At subsequent inspection `mateerp-database-1` existed and was healthy. Treat the absence at backup time as transient, not a permanent misnamed reference. No script change is justified by this evidence alone.
- Previous DR log: 2026-10-21 completion, `daily_full=2026-10-21`, latest size 66,655,296 bytes. Cloud object existence was not independently rechecked in this review.
- Separate Native SQL custom dump dated 2026-10-21: `pg_restore -l` successfully parsed its TOC. Parsing is NOT an independent restore test.

## Required next acceptance
1. Investigate ERP DB downtime / overlapping deployments around the 2026-10-22 scheduled DR attempt.
2. Verify Azure latest and dated daily snapshot availability, checksum and retention with authorized non-destructive verification.
3. Verify successful next scheduled backup or request explicit approval for controlled retry (resource/availability impact review).
4. For real disaster recovery, use an independently isolated restore rehearsal; never restore over production.
5. Preserve root-only recovery archives, `.env` files and private keys. Do not put raw backup payloads or secrets in GitHub.
6. Confirm risk-aware rollback. If any organization has Required MFA, **do not blindly roll back to a Native image without corresponding enforcement**.

## Outcome
Local job success and historical restore evidence are real but insufficient to claim current whole-server DR readiness. The Azure failed run is a closeout blocker until mitigated and verified.

## Additional Phase 8 local Restic verification (2026-10-23)
- Read-only `restic snapshots --json` listed **15** snapshots; latest snapshot time 2026-10-22 03:32:21 +02:00.
- Read-only `restic --no-lock check` returned **exit 0**, validated indexes, pack references, 15/15 snapshots, trees and blobs; final message: `no errors were found`.
- This is repository metadata/integrity validation, **not** a full backup-data read and **not** an independent restore acceptance.
- Azure DR remains a separate failure because the most recent scheduled whole-server job failed on 2026-10-22 while `mateerp-database-1` was temporarily unavailable. No script mutation or forced backup run was performed.

## 2026-10-23 Owner-directed verification scope and current access blocker
The owner explicitly prohibited **all restoration on MateServer**, declined repeat isolated restore drills, and requested only:
1. Verify the newest Azure objects for both `latest/mateserver-latest.tar.gz` and `daily/YYYY-MM-DD/mateserver-full.tar.gz` and their SHA256 objects; confirm hashes and archive completeness.
2. Read-only disaster recovery inventory: ensure the newest actual archive contains application and Native database custom dumps, Compose and environment configuration, persistent Maildir/DKIM/TLS volumes and host recovery instructions. No extraction over production, no database creation and no restoration.

On 2026-10-23, Desktop Commander reported MateServer **Offline** (last seen approximately one hour previously). The available tools did not include an authenticated Azure Blob connector. Therefore **NO live Azure object, latest snapshot, downloaded archive or hash was verified in this attempt**. The previous 2026-10-21 success log and 2026-10-22 failed scheduled job are historical evidence only; do not project their states onto 2026-10-23.

The current `docs/BACKUP_RESTORE.md` documents the expected archive payload and recovery procedure, and historical isolated backup validation on 2026-10-09. That documentation review does not certify the latest backup. This follow-up is **Blocked: server connection unavailable**, not `Passed`.

**Safety statement:** No backup job was started, no remote archive was downloaded, no test database or VM was created, no file or DB was restored, and no production service or configuration was modified.

When authorized read-only connectivity is available, establish the remote object's current date, ETag/size, matching SHA256, full archive readability and required inventory before recording Verified. Never expose Azure SAS URLs, keys or archive secrets.

## Live Azure Blob and DR archive verification: 2026-10-23
**Owner-approved scope:** Read-only Azure object verification and archive / DR inventory only. **No restore, extraction, database creation, VM creation, service restart, or manual backup execution.**

The previous Desktop Commander authorization failure was resolved by owner device authorization. MateServer was Online for the following verification.

### Scheduled backup and remote integrity
- `mateserver-backup.service` last scheduled run completed **successfully**, exit status 0, on **2026-10-23 at 03:33:41 CEST**.
- `matemail-backup.service` also completed successfully on 2026-10-23.
- Backup run log reports `latest_bytes=66762044`, `daily_full=2026-10-23`, 24 retained daily snapshot dates.
- Azure HEAD request independently confirmed HTTP 200 and **66,762,044 bytes** for both `latest/mateserver-latest.tar.gz` and `daily/2026-10-23/mateserver-full.tar.gz`.
- Both remote blob byte streams were completely read and SHA256 digests matched their respective Azure `.sha256` companion objects. Both archives had **identical SHA256**.
- The locally built archive had the same SHA256 as each verified Azure object. All network SAS/credentials were used only in protected process memory, not displayed or added to GitHub.

### Full archive and recovery payload checks (read only)
- gzip/tar archive read successfully. **3,544 tar entries**; 2,777 regular files and 15 content-linked hard link entries.
- `meta/SHA256SUMS` had **2,786** file entries. All checksums matched, including 15 hard link paths resolved against their referenced content. **Zero missing or mismatched content hashes.**
- **11/11 PostgreSQL custom-format dumps** passed both `PGDMP` signature inspection and `pg_restore -l` table-of-contents listing without restoring: evolution, MateAssist, MateConnect, MateCRM, MateDesk, MateERP, MateMail Native, MateMail App, MyRight, NetaMate and TalkRoom. `postgres-globals.sql` also present.
- MateMail App and Native Engine `.env` and Compose files present at their expected relative locations. Monitoring environment / Compose files also present.
- Persistent Maildir, DKIM, TLS, Postfix queue and host Nginx, systemd and Let's Encrypt content categories all present. The Azure archive also contains recovery runbook and script references.
- The MateServer root-only `DR-RUNBOOK.md` exists. Six recovery scripts (`restore.sh`, `restore-preflight.sh`, `restore-health.sh`, `restore-host.sh`, `restore-seed.sh`, `restore-services.sh`) passed nonexecuting `bash -n` syntax checks.

### Acceptance classification
**Verified:** Latest and dated Azure objects exist and byte hashes match. Complete archive is readable, all internal SHA256 checks pass, the 11 database dumps have valid custom-format TOCs, required data/configuration categories and recovery instructions are present.

**Not Verified (by deliberate scope):** Fresh independent database restore, blank-VM failover, application startup from recovered data, actual recovery time, and end-to-end failover. Prior isolated restore evidence from 2026-10-09 is retained; the owner does not want repetitive restore tests.

**Current scheduled backup incident status:** The 2026-10-22 failure caused by a temporarily unavailable MateERP database container is recovered operationally by the successful 2026-10-23 run. Preventing future transient failure remains a separate monitoring / resilience question. No backup script modification was performed.
