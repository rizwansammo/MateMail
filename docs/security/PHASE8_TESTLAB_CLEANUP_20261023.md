# Phase 8 TestLab Cleanup and Preservation Report
**Date:** 2026-10-23. **Status:** INVENTORIED; RAW RESULTS ARCHIVED SECURELY; DELETION NOT PERFORMED.

## Read-only inventory
Primary area: `/opt/TestLab/MateMail-Stage7A`, approximately 2.3 GB.
Observed directory sizes: `source` ~1.1 GB, `ui-qr-setup` ~847 MB, `browser-e2e` ~346 MB, `ga-enforce-test-20261022` ~22 MB, `results` ~1.6 MB. Results directory had 101 top-level files, including 90 Stage 7C-named files.

Git worktrees:
- Main test repository `source` on the old Phase 7A feature branch.
- Detached `ga-enforce-test-20261022` matching release SHA.
- `ui-qr-setup` branch has uncommitted modifications and untracked additions. **Not safe to discard** without separate reconciliation.

Eight stopped test containers identified by names starting with `mm7c` / `mm7cnative`; Compose labels and mount inspection indicate isolated lab ownership. Two isolated networks: `mm7c_isolated`, `mm7cnative_isolated`. There are associated anonymous mounts requiring a strict attachment/ownership audit before removal.

## Evidence retained in place
- `results/stage7c-live-session-revocation.log`
- `results/stage7c-native-credential-smoke.log`
- `results/stage7c-3service-combined-smoke.log`
- `results/stage7c-recovery-aggregate-real-pg.log`
- `results/stage7c-post-live-native-full-regression.log`
- Source snapshots and relevant `docs/PHASE7C_*.md` records.

The Markdown Phase 8 reports retain findings and evidence pointers but are NOT a substitute for safely archiving relevant raw logs. Do not commit raw logs containing possible credentials or synthetic secret material to a public repository.

## Cleanup hold
No files, networks, containers, volumes, worktrees, test credentials or backups have been deleted. Required first: private protected evidence archive, verification of all worktree differences and resource ownership, reconciliation of outstanding security acceptance, and a scoped cleanup plan. Never use `docker system prune -a`, delete live volumes or erase another project's files.

## Post-cleanup verification: Pending
Recheck all mail services, databases, Compose networks, volumes, mailbox storage and VPS resource state after separately approved cleanup.

## Protected Phase 8 results archive
Original TestLab results and README were preserved in a root-only archive under `/root/matemail-phase8-evidence/phase7-test-results-20261023.tar.gz`. Verified tar listing/integrity completed; file mode 0600, owner root; archive 991,721 bytes; SHA-256 `52d391de9bc6bbe6ea32e025ae92524a609ce8b330da06ded8b8d8ddc116c826`. All 101 original top-level result files remain in TestLab. This is same-server preservation, **not verified offsite backup**. Browser artifacts, dirty worktrees and other fixtures remain untouched.
