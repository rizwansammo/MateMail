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
| D3 Worktree safety | Blocked: uncommitted ui-qr-setup changes | Reconcile and preserve changes before removal |
| D4 Temporary resource cleanup | Pending | Delete only positively owned, unneeded resources |
| D5 Health regression after cleanup | Pending | Check SMTP/IMAP, DB, volumes, other projects |
| E1 Seven Phase 8 Markdown reports | Drafted on documentation branch | Review findings and revise with final evidence |
| E2 Final PR/CI/merge | Pending | Avoid premature CI expenditure |
| E3 Formal acceptance | Blocked | Close only with defensible evidence and owner approval |

**Safety:** no live policy flips, no customer test mailbox, no image rebuild, no restart, no production configuration change and no resource deletion without the defined safety gates.
