# Phase 3 — Controlled Native Engine Production Upgrade

**Deployment date:** 2026-10-09 (MateServer local date/time may differ).
**Result:** Production cutover of all four engines succeeded; each service passed its immediate checks.
**PR:** #64 — remains DRAFT until Phase 4 end-to-end acceptance.
**Scope:** Native Engine security patch upgrade only; unrelated product containers and databases unchanged.

## Running versions and exact immutable production images

| Engine | Previous | New | Image now running |
|---|---|---|---|
| Unbound | 1.22.0 | **1.26.1** | `ghcr.io/rizwansammo/matemail-native-unbound@sha256:19399825f8402b65ebeefec1ab2f0bdee827e0eaa48e32be85fcb434ef0d564a` |
| Dovecot | 2.4.1 | **2.4.5** | `ghcr.io/rizwansammo/matemail-native-dovecot@sha256:3dc5a9485f5207fb0987cd53466eacbb71b4d936490b9e9c2cf485db2347db50` |
| Rspamd | 3.11.1 | **4.2.1** | `rspamd/rspamd:4.2.1@sha256:dfb8176e560c178faa4e18ebf9c9b43d0d60c2d2fa03f748c34a27e5c3418dc4` |
| Postfix | 3.7.11 | **3.10.13** | `ghcr.io/rizwansammo/matemail-native-postfix@sha256:5feeef5a425202328aafe54f7b52bb35a9976d8bccd43b34666e1a629a431b81` |

The three custom images were **built and published from commit `ece64bd2273a9b24ed502932ba93fc6bd62061cc`**, with release tag `phase3-ece64bd2273a9b24ed502932ba93fc6bd62061cc`. The existing GHCR `:ne1` tags were **not overwritten**. Private images were transferred onto the VPS by GitHub Actions using a job-scoped token and temporary Docker authentication, with no persistent registry credential.

## Safety / recovery points

- Original `.env`, Compose, and previous running image IDs saved root-only in `/root/MateMailEngineUpgrade-20261009/` as `native.env.before`, `native.compose.before.yml` and `running-images.before`; post-cutover configs also saved as `native.env.after`, `native.compose.after.yml`.
- Fresh local Restic **before** cutover: `c7196e65`; **after** cutover: `c14800c7`. Both snapshot processes exited successfully and verified their contents.
- All four prior image references remain available locally on MateServer. **Never prune them before Phase 4 acceptance.**
- Local Restic currently has **no separate offsite repository** (existing issue, not addressed in this upgrade). The separate pre-reset backup retained previously was not deleted.
- Only `unbound`, `dovecot`, `rspamd`, and `postfix` containers were recreated one at a time using `docker compose up -d --no-deps --force-recreate <service>`; no volume deletion/reset and no Native DB migration.

### Rollback procedure (if a future failure appears)

Choose the affected engine before undoing all four. For a full rollback, restore the saved root-only Compose and environment under `/opt/MateMail/engine/deploy/native-engine`, validate `docker compose config -q`, then recreate only the four affected services **in reverse order**, `postfix` → `rspamd` → `dovecot` → `unbound`, confirming health and mail flow after each. Preserve all named volumes and current queues. Do **not** use `docker compose down -v`, `docker volume prune`, or `docker system prune`. If Rspamd's Redis state becomes backward-incompatible, inspect/restore the pre-cutover state rather than blindly reverting the binary against potentially migrated data.

## Verified acceptance in Phase 3

- **Unbound:** `unbound -V` 1.26.1, healthy; DNSSEC-valid `cloudflare.com` AD; deliberately invalid `dnssec-failed.org` SERVFAIL.
- **Dovecot:** `dovecot --version` 2.4.5, healthy, `doveconf -n` valid; Unix auth socket still accessible inside Postfix; `doveadm service status` passes. Public IMAPS/993 TLS 1.3 certificate `mx.matemail.pro` verifies.
- **Rspamd:** `rspamd --version` 4.2.1, healthy, `rspamadm configtest` passes; Postfix can connect to milter TCP/11332; safe `rspamc symbols` scan processed without sending any email.
- **Postfix:** `postconf mail_version` 3.10.13, healthy, `postfix check` clean; SASL type Dovecot, default milter action tempfail, DANE settings and Forward Group policy hook retained. Production `master.cf` and `header_checks` bind-mounted files fixed to mode 0644 root:root after new version warned they were owned by unrecognized host UID/GID.
- **Mail policy:** SMTP/25 accepts RCPT for `noreply@mail.matemail.pro` (250), denies external unauthenticated relay (554). SMTP Submission/587 offers STARTTLS, TLS 1.3 and authentication, and blocks unauthenticated relay. **No DATA command was issued; no test email was sent.**
- **Infrastructure:** Postfix queue empty; Native API /ready true, schema 6, Forward Groups and mailbox sender authorization available. 21/21 MateMail containers healthy; all four web HTTPS endpoints HTTP 200; Nginx valid; backup/monitoring/custom-host provisioner timers active. Application remains 1 platform admin / 0 customer tenants / 0 customer domains / 0 mailboxes; Native Engine remains 1 internal domain / 2 internal system mailboxes.

## Phase 4 still required — not covered by these checks

Real synthetic account SMTP AUTH good/bad credentials, actual IMAP login, SMTP→Rspamd→Postfix→Dovecot LMTP and external mail send/receive, signed DKIM alignment and SPF/DMARC, spam/ham/quarantine and ClamAV/Olefy, retry/bounce/deferral, event push, monitoring stability, and tested rollback on a clone. Phase 3 **does not** establish that all of these end-to-end paths succeed. Do not merge this draft PR to main or remove rollback artifacts before the Phase 4 acceptance gate.

The separately planned **final cleanliness and full security audit** comes only after all four upgrade phases.
