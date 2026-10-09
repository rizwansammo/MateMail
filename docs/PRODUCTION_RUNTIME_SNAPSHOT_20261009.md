# MateMail Production Image & Compose Snapshot — 2026-10-09

**Evidence:** read-only production MateServer audit, after Native Engine Phase 3 rollout and Gmail round-trip acceptance. This is a dated record, **not a credential file or an automated deployment config**.

## Deployed image selections

| Service | Version / image revision |
|---|---|
| Application backend / Celery | `ghcr.io/rizwansammo/matemail-backend:7a32f15c8c2c2274dc863cd82d6c875e8f6d04bc` |
| Application frontend | `ghcr.io/rizwansammo/matemail-frontend:7a32f15c8c2c2274dc863cd82d6c875e8f6d04bc` |
| Native Postfix | 3.10.13, `sha256:5feeef5a425202328aafe54f7b52bb35a9976d8bccd43b34666e1a629a431b81` |
| Native Dovecot | 2.4.5, `sha256:3dc5a9485f5207fb0987cd53466eacbb71b4d936490b9e9c2cf485db2347db50` |
| Native Unbound | 1.26.1, `sha256:19399825f8402b65ebeefec1ab2f0bdee827e0eaa48e32be85fcb434ef0d564a` |
| Rspamd | 4.2.1, `rspamd/rspamd:4.2.1@sha256:dfb8176e560c178faa4e18ebf9c9b43d0d60c2d2fa03f748c34a27e5c3418dc4` |

The engine's `NATIVE_POSTFIX_IMAGE`, `NATIVE_DOVECOT_IMAGE` and `NATIVE_UNBOUND_IMAGE` are pinned **in the root-only production `.env`**, not automatically inferred from Dockerfile or old `:ne1` Compose fallback tags.

## GitHub ↔ production configuration comparison

Compared the source files from the Native Engine upgrade branch against the corresponding deployed Compose files, without modifying production:
- App Compose: **semantically identical** (only trailing blank line differs).
- Native Engine Compose: **semantically identical** (only trailing blank line differs).
- Monitoring Compose: GitHub Grafana memory limit was **200M**; live production was **400M**. Source has now been aligned to production **400M**.
- The application image pins in production are from an earlier vetted deployment, **not** automatically the newest GitHub `main` SHA. This is expected by the manual-deployment policy and should not be silently changed during source synchronization.

## Health & limits of acceptance

At audit time: **21/21** MateMail containers healthy; Native mail queue empty; Gmail round-trip confirmed external inbound and outbound, SPF/DKIM/DMARC all PASS, TLS 1.3. This is not an acceptance result for first-customer TeamBox, Delegation, Forward Groups or other tenant workflows.

## Deployment protections

- A merge to GitHub `main` will **not** automatically update MateServer.
- Do not redeploy or recreate the Native Engine from older tag defaults. Use immutable digests and the Native Engine's controlled rollout procedure.
- Before an app cutover, validate the Native API collaboration contract, migrations and pinned image SHA, and verify backup eligibility.
- The app deployment workflow's pre-deploy database archive location is `/opt/MateMail/backup/pre-deploy/`; the old `/opt/MateMail/backups` directory is not present.
- Separate Azure DR and local Restic jobs are documented in `docs/BACKUP_RESTORE.md`.

Do not use this dated record to claim the current state without re-querying production Docker and health checks.
