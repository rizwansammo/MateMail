# Phase 8 TestLab Cleanup and Preservation Report
**Date:** 2026-10-23. **Status:** Initial evidence protected; scoped Docker cleanup completed; filesystem cleanup remains pending.

## Read-only inventory
Primary area: `/opt/TestLab/MateMail-Stage7A`, approximately 2.3 GB.
Observed directory sizes: `source` ~1.1 GB, `ui-qr-setup` ~847 MB, `browser-e2e` ~346 MB, `ga-enforce-test-20261022` ~22 MB, `results` ~1.6 MB. Results directory had 101 top-level files, including 90 Stage 7C-named files.

Git worktrees:
- Main test repository `source` on the old Phase 7A feature branch.
- Detached `ga-enforce-test-20261022` matching release SHA.
- `ui-qr-setup` branch has uncommitted modifications and untracked additions. **Not safe to discard** without separate reconciliation.

Eight stopped test containers identified by names starting with `mm7c` / `mm7cnative`; all had either matching test Compose labels or exactly known test HTTP names, and all bind mounts remained in the MateMail TestLab. Both isolated networks had zero attachments. Three anonymous volumes were verified to be attached only to these test containers. All 8 containers, both networks and all 3 anonymous test-only volumes were subsequently removed safely.

## Evidence retained in place
- `results/stage7c-live-session-revocation.log`
- `results/stage7c-native-credential-smoke.log`
- `results/stage7c-3service-combined-smoke.log`
- `results/stage7c-recovery-aggregate-real-pg.log`
- `results/stage7c-post-live-native-full-regression.log`
- Source snapshots and relevant `docs/PHASE7C_*.md` records.

The Markdown Phase 8 reports retain findings and evidence pointers but are NOT a substitute for safely archiving relevant raw logs. Do not commit raw logs containing possible credentials or synthetic secret material to a public repository.

## Cleanup hold
The eight confirmed stopped test containers, two unoccupied isolated networks and three verified test-only anonymous volumes have been deleted. Files, source worktrees, test credentials and backups remain untouched. The dirty `ui-qr-setup` worktree cannot be removed without reconciling its eleven changes. Never use `docker system prune -a`, delete live volumes or erase another project's files.

## Post-cleanup verification: Partial PASS
Immediately after scoped Docker cleanup, ten checked live containers (Backend, Frontend, Worker, Beat, Native API, Native Postfix, Native Dovecot, Native DB, ClamAV and Rspamd) were all running and healthy. Filesystem cleanup remains pending, so full final regression is not yet complete.

## Protected Phase 8 results archive
Original TestLab results and README were preserved in a root-only archive under `/root/matemail-phase8-evidence/phase7-test-results-20261023.tar.gz`. Verified tar listing/integrity completed; file mode 0600, owner root; archive 991,721 bytes; SHA-256 `52d391de9bc6bbe6ea32e025ae92524a609ce8b330da06ded8b8d8ddc116c826`. All 101 original top-level result files remain in TestLab. This is same-server preservation, **not verified offsite backup**. Browser artifacts, dirty worktrees and other fixtures remain untouched.

## Docker cleanup evidence archive
Root protected archive: `/root/matemail-phase8-evidence/stopped-lab-docker-20261023.tar.gz`. Contains Docker inspect metadata, container log extracts, network/anonymous volume manifests and inventory. File size 10,337 bytes, mode 0600. SHA-256 `94b57289171fb8d148a0d9707d3a3336d38a2261c4085b65019ca80598a1acbd`. It may contain sensitive synthetic configuration so it must stay outside GitHub. The initial full results archive remains unchanged.

## Subsequent scoped worktree handling and source preservation
- Verified detached release QA worktree `ga-enforce-test-20261022` clean and pinned to `1638299f6046e8493d74e64077ef7599b45ba6d7`. Used normal `git worktree remove` to unregister and remove it. Parent `source` and `ui-qr-setup` remain intact.
- Expanded porcelain status of the `ui-qr-setup` worktree found **14 individual modified/untracked files** (11 porcelain status entries where untracked directories aggregate several files).
- Content comparison to the release showed 10/14 matching and 4/14 differing: `backend/apps/accounts/urls.py`, `backend/apps/accounts/views.py`, `frontend/app/app/security/page.tsx`, `frontend/components/postbox/mail-client-security-settings.tsx`. These differences must be reviewed before deletion; they do not by themselves prove unmerged fixes, since the worktree is based on an older revision.
- The original `source` worktree head `3f917e73d70cd44bf95177f58ffd3884b54e64c2` was not a Git ancestor of the final release, so source must not be blindly removed.
- Root protected full working-file plus tracked diff archive: `/root/matemail-phase8-evidence/ui-qr-uncommitted-changes-20261023.tar.gz`. Mode 0600, 87,639 bytes, SHA-256 `218e7d42764225eb5edd4e830333dc83faafeb4be1189e6583af0a44e3fc0ac2`. Verified internal members. Original worktree preserved.
- A separate root-only JSON record of normal GA worktree removal is retained.
- Browser QA files, primary source, UI worktree, test fixtures and associated dependencies remain in TestLab. Filesystem cleanup is incomplete and no assertion is made that all lab space has been released.
