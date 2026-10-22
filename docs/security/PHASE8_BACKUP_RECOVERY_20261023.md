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
