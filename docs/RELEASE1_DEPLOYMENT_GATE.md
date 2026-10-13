# MateMail Release 1 Deployment Gate

## Included in Release 1

- Phase 2 system domain ownership registry and protected Native resources.
- Phase 3 system configuration drafts, immutable history and Platform Admin
  readiness inventory.
- Strictly permissioned, audited and disabled-by-default inactive system
  mailbox staging.
- Native Engine schema version 9 with optional restricted API runtime identity.

## Must stay OFF for the first deployment

- `SYSTEM_MAIL_STAGING_ENABLED=False`
- `NATIVE_PLATFORM_OPERATIONS_ENABLED=False`
- Any future system domain or sender cutover and automated platform
  mailbox creation.
- Any tenant-facing optional transport security not already independently
  approved and deployed.

## Runtime DB role

The new restricted Native API runtime role must be verified before setting
`NATIVE_RUNTIME_DB_ENABLED=True`. Its credential is NOT the Native API secret,
the Postfix/Dovecot reader secret or the Platform operations secret.
The older API still carries a bootstrap superuser connection for migrations.
A separate high-assurance startup migrator is a future defense-in-depth step.

## Deployment sequence (not executed)

1. Current verified Azure and Restic backups, plus scoped rollback rehearsal.
2. Final isolated Django-to-Native integration and full regression/CI gate.
3. Approve immutable GHCR image digests and release manifest.
4. Merge the reviewed branch once all checks pass.
5. Apply Django and Native Engine migrations in a coordinated change window.
6. Keep Platform operations disabled; validate Postfix/Dovecot, existing DKIM
   signing, SMTP submission, inbound IMAP, PostBox login and reporting.
7. Only after verification, consider enabling the restricted Native runtime
   role with separate credential and recheck readiness.
8. Roll back using the documented plan if there is any regression.

## Status

**NOT DEPLOY READY (2026-10-13)**. This file documents gates, not approval.

## Final October 13 QA gate update

Latest evidence: `docs/RELEASE1_FINAL_QA_20261013.md`.

- The isolated real Django to Native API staging flow has passed.
- Native request privilege tests passed 14/14.
- The CI-parity full suite uncovered stale repository test assumptions. They
  were corrected and the affected tests passed 51/51; the **entire final
  branch still requires CI**.
- Opening a PR against main triggers CI and Native Security checks. Main CI
  skips GHCR publishing for pull_request events.
- Do not enable system operations or DNS cutover in Release 1.
- Retain Native schema 8/9 with protection triggers during application rollback;
  they are forward migrations, so rollback means restoring older application
  images (verified schema version >= minimum), NOT dropping the schema.
- The deployment window must have an exact approved image digest list,
  new backup and a human who can verify mail send/receive immediately.
