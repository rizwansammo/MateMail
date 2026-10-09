# MateMail Native Engine — Security Upgrade (2026-10-08)

**Status:** Phase 1 completed — investigation, branch, baseline, compatibility gates. **No production image or runtime configuration changed in this phase.**

**Scope:** Dovecot, Rspamd, Unbound and Postfix only. Production Native Engine is under `/opt/MateMail/engine/deploy/native-engine`. Changes to running images, networks, data volumes, databases and mail delivery are deferred until Phases 2–3.

## Live baseline (checked on MateServer)

| Component | Running executable | Source/build | Image in production | Upgrade candidate |
|---|---|---|---|---|
| Dovecot | 2.4.1 | GHCR thin derivative of `dovecot/dovecot:2.4.1` | `NATIVE_DOVECOT_IMAGE` pinned by SHA256 | Dovecot **2.4.5** |
| Rspamd | 3.11.1 | Official `rspamd/rspamd:3.11` | `rspamd/rspamd:3.11@sha256:116c2e05…` | Official **4.2.1** |
| Unbound | 1.22.0 | GHCR from `alpine:3.21` `apk add unbound` | `NATIVE_UNBOUND_IMAGE` pinned by SHA256 | Upstream **1.26.1**, packaging must be solved |
| Postfix | 3.7.11 | GHCR from `debian:12-slim` `apt install postfix postfix-pgsql` | `NATIVE_POSTFIX_IMAGE` pinned by SHA256 | Patched supported Postfix, packaging choice below |

All four Native Engine containers were **healthy**, Postfix queue **empty**, customer tenants/domains/mailboxes **0**, and only internal `mail.matemail.pro` and its 2 system mailboxes remain. Verified `dovecot/dovecot:2.4.5`, `rspamd/rspamd:4.2.1`, `debian:13-slim`, and `alpine:3.24` upstream **manifest availability**, but did NOT download, build, or validate runtime compatibility. Version availability is not a passing upgrade test.

Production rollback pins are in the server-side root-owned `deploy/native-engine/.env`, and the old Compose/image digests must be recorded before any Phase 3 change. Do not print secrets or store production `.env` in Git.

## Confirmed upgrade risks and technical design gates

### 1. Dovecot 2.4.1 → 2.4.5

- The existing derivative image is **not just an upstream tag**: the entrypoint, UID/GID and filesystem layout are essential to PostBox login and mail persistence.
- Required invariants: `vmail:5000:5000`; `dovecot` and `dovenull` identities; `smtpauth` group GID **2102**; group-readable `/etc/dovecot/postbox-master`; mail/index volume ownership; `/run/dovecot-auth/auth-client` usable by Postfix; TLS; SQL passdb/userdb; LMTP; IMAP/IMAPS; managesieve; push notification.
- The upstream 2.4.5 image is available, but its base identities and installed tooling **must be re-measured in Phase 2**. Do not blindly replace the old FROM digest or assume the `groupmod` commands still succeed.
- Tests: image builds with revised immutable digest; `dovecot --version`, `doveconf -n`; auth socket ownership; synthetic account login and wrong-password rejection; LMTP delivery to isolated maildir; IMAPS STARTTLS/cert and PostBox master credential integration; Postfix SASL connection.

### 2. Rspamd 3.11.1 → 4.2.1

- Stay with **official `rspamd/rspamd` image**. Pin a SHA256 digest after testing. No custom GHCR image needed.
- This is a major jump through 4.0/4.2, **not a no-op patch update**. Migration notes cover symbol scheduler changes, Redis/Bayes shard mapping, multimap `top` suffix semantics, content URL behavior, and changed options.
- Current Native Engine local configurations: `actions.conf`, `antivirus.conf`, `dkim_signing.conf`, `external_services.conf`, `force_actions.conf`, `groups.conf`, `milter_headers.conf`, `options.inc`, `redis.conf`.
- Confirm that no configured `filter = "top"` multimap and no per-user Bayes shard migration are required. Do not assume from filenames; inspect full active config in test environment.
- Preserve dedicated `native_rspamd`, `native_dkim` and Redis volumes. **Clone** learned state for isolated tests rather than running new version on live volume. Verify the key read permissions for UID **11333**, selector map, `use_esld=false`, DKIM signatures, spam/ham outcomes, AV/Olefy connectivity, quarantine tagging and milter action.
- Guard against false positive rejects and mail loss: Postfix has `milter_default_action=tempfail`, so mail-flow checks must include Rspamd unavailable and working scenarios.

### 3. Unbound 1.22.0 → security-patched 1.26.1

- Current Dockerfile `FROM alpine:3.21` installs the distribution's **1.22.0**. Rebuilding that same Dockerfile is **not** a version upgrade.
- NLnet Labs 1.26.1 is a security release (including DNSKEY/DNSSEC defects). Alpine 3.24 stable publishes `1.25.2-r2` as of audit, while Alpine edge has `1.26.1-r0`. **Do not silently substitute Alpine edge packages into a stable production image.**
- Phase 2 candidate: reproducible pinned source build of upstream 1.26.1 with checksum/signature verification, or an explicitly documented stable distro **security-backported** package with evidence it fixes the relevant CVEs. Select based on safe proof, not nominal version alone.
- Preserve non-root `unbound` user, `/var/lib/unbound/root.key` trust-anchor volume, TCP/UDP 53, private `172.27.0.0/16` allowlist and exact Docker network/IP assumptions.
- Positive DNSSEC (e.g. `cloudflare.com`), deliberately bogus DNSSEC (`dnssec-failed.org`), MX/TLSA for DANE, outbound resolution, healthcheck probing **Unbound** rather than Docker's resolver, timeouts, cold-start trust-anchor and container-restoration tests are mandatory.
- Fail closed if DNSSEC validation breaks; Postfix uses `smtp_dns_support_level=dnssec` and `smtp_tls_security_level=dane`.

### 4. Postfix 3.7.11 → supported security-patched release

- Dockerfile uses `debian:12-slim` and Debian 12 Bookworm Postfix **3.7.11** (not a self-contained Postfix version pin). The Debian Security Tracker currently marks Bookworm `CVE-2026-43964` vulnerable/no-DSA.
- Upstream 3.7.23 exists but the 3.7 branch is **out of support** and upstream notes it does not contain all out-of-support fixes. Therefore simply forcing 3.7.23 is **not the preferred long-term solution**.
- Phase 2 **preferred packaging candidate**: `debian:13-slim` (Trixie) with Debian's supported `postfix 3.10.13-0+deb13u1` (security-patched). Upstream 3.11.7 is newer; using it would require an independently maintained build/packaging chain. Evaluate before choosing.
- Keep `postfix-pgsql`, `python3-psycopg2`, Tini, control daemon, DB maps, queue, fixed `smtpauth` GID **2102**, `/run/dovecot-auth/auth-client`, SMTP 25/submission 587, STARTTLS, SASL, LMTP static destination, `inet:rspamd:11332` and fail-closed behavior.
- Validate config compatibility for deprecated/replaced Postfix parameters and Debian 13 package/user ownership differences. Must preserve the **existing queue volume**. Test queue deferred mail, relay restrictions, inbound, outbound, sender authorization, TLS, DNSSEC/DANE and Rspamd-milter outage/recovery.

## Phase 2 — candidate builds and isolated integration tests

1. Start from this feature branch; prepare candidate Dockerfiles and Compose override **only** in nonproduction. Pin all upstream base images by immutable digest. Do not change live `.env`.
2. **Before running `native-engine-images.yml`**, change its tagging policy for this branch: it currently publishes both `:${{ github.sha }}` and the moving `:ne1` tag. Candidate builds must publish ONLY a unique branch/commit candidate tag and capture immutable digests; they must **not move `:ne1` or `:latest`** during testing. Never retag production's pinned references until the test gate passes.
3. Isolated Compose project and network with **no published ports**, separately named temporary volumes and test-only DB/Redis/DKIM/maildirs; no mounts from live `matemail_native_*` volumes. Do not use `docker compose down -v` on production.
4. Run dependency checks: Unbound → Dovecot/Postfix SASL/LMTP → Rspamd/DKIM/milter → Postfix queue/delivery; test pass and fail paths with synthetic message fixtures.
5. Tests must exercise positive and negative SMTP AUTH, IMAP login, bounce/defer behavior, DKIM/DMARC alignment, spam/ham, Redis persistence, ClamAV/Olefy and DNSSEC/DANE. Verify restarts, re-creations and logs.
6. Declare pass/fail with command evidence, image digests, compatibility adjustments, and actual rollback instructions. The four engines may **not** proceed to Phase 3 unless all gates pass.

## Phase 3 — production rollout with rollback at each engine

Order (one at a time): **Unbound → Dovecot → Rspamd → Postfix**. Rebuild full impacted custom GHCR images but restart/recreate **only** the approved one at each gate.

Before cutover: verify a fresh consistent recovery snapshot, save current Compose and SHA256 image digests separately, check queues and health, verify there are no customer mailboxes or mail activity; define and test the rollback command **using the previous pinned digest**. Never mutate existing volumes to perform rollback. For Rspamd, pay special attention to Redis state compatibility: rollback must either use verified backward-compatible state or restore a tested clone.

After each engine: exact executable version, ready/healthy, `docker logs`, relevant functional tests (including failed auth and negative DNSSEC), 25/587/993 TLS handshakes, expected message queue size, and no new errors. Any failure: stop rollout and restore previous digest; do not continue.

## Phase 4 — release acceptance

Verify complete inbound/outbound synthetic email flow, message headers and DKIM, quarantine, PostBox/Hub integration, queue/defer/retry, Sieve/LMTP, DNSSEC/DANE, certificate renewal, fail2ban, vulnerability inventory, and monitoring. After successful Phase 3 rollout and Phase 4 acceptance, merge the fully tested implementation and verified production image pins to main; do not trigger an unnecessary second rollout. The separately planned **final cleanliness + security audit** is a later task, not part of these four upgrade phases.

## Primary upstream references (read 2026-10-08)

- Dovecot 2.4.5 security announcement: https://dovecot.org/mailman3/archives/list/dovecot-news%40dovecot.org/thread/D7FJQODYCY23BG3FWUZXCJRCLCPT7RZT/
- Dovecot image tags: https://hub.docker.com/r/dovecot/dovecot/tags
- Rspamd 4.2.1: https://docs.rspamd.com/changelog/4.2.1/
- Rspamd 4.2 migration: https://docs.rspamd.com/tutorials/migration/
- Unbound 1.26.1: https://github.com/NLnetLabs/unbound/releases
- Alpine stable/edge package versions: https://pkgs.alpinelinux.org/packages?name=unbound
- Debian Postfix tracker: https://security-tracker.debian.org/tracker/source-package/postfix
- Debian 13 Trixie Postfix: https://packages.debian.org/trixie/postfix
- Postfix latest upstream announcements: https://www.postfix.org/announcements.html

**Non-goals in Phase 1:** changing production Dockerfiles/Compose; replacing running images; deleting data; upgrading PostBox/frontend/backend; merging Phase 2 code without integration tests.
