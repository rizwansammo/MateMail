# Native Engine & container image security audit — 2026-10-09

## Scope, method, and safety

- **Read-only inspection of running container image IDs** on MateServer. No production service was restarted, reconfigured, replaced, or stopped, and no secrets or customer mail were examined.
- Scanner: official Aqua Trivy **v0.75.0** downloaded with the upstream SHA-256 checksum verified; severity selection **HIGH/CRITICAL**, vulnerability scanner only. Identified vulnerabilities may not be exploitable on the deployed surface; a scanner result is not a penetration test.
- Native Dovecot's layer-aware scan failed because a layer blob could not be read from its local Docker archive. A **fresh, never-started, temporary container created from the same image** was exported and its clean root filesystem scanned instead; it was removed afterward. Rootfs results may differ from image-layer results.
- Results below reflect **currently running images**, not the P3 patched application candidate images that GitHub CI has published but have **not** yet been deployed. Distinguish image/OS advisories from Python package findings.

| Running image / service | Critical | High | Findings with an upstream fixed version in scan |
| --- | ---: | ---: | ---: |
| Native Postfix | 0 | 69 | 0 |
| Native Dovecot (image filesystem scan) | 4 | 55 | 0 |
| Native Rspamd | 1 | 50 | 0 |
| Native API (pre-P3 runtime image) | 0 | 15 | 15 |
| Native Unbound | 0 | 0 | 0 |
| Native ClamAV | 0 | 1 | 1 |
| Native Olefy | 0 | 11 | 11 |
| Native submission gateway (HAProxy) | 0 | 4 | 4 |
| Native policy runtime | 0 | 11 | 11 |
| Native PostgreSQL | 1 | 30 | 31 |
| Native Redis | 0 | 6 | 6 |
| MateMail backend (pre-P3 deployment) | 1 | 62 | 11 |
| MateMail frontend (pre-P3 deployment) | 3 | 16 | 18 |

These are **advisory records**, not counts of exploited vulnerabilities. A package may have several records, and findings lacking a fixed version are not necessarily unfixable forever.

## What the audit establishes

1. Application dependency PR #71 and Native API cryptography PR #72 are merged and tested, but their production rollout remains separate and manual; the running application still contains pre-P3 pinned images. Do not claim the running app is fully patched until the new immutable backend/frontend images are deployed and verified.
2. Native API/Olefy/policy: stale Python 3.13 Alpine base has patchable packages (notably `libuuid`). This PR pins a newly checked official Python 3.13 digest and adds build-time Alpine security upgrades for the custom API/Olefy images. GitHub runs build-only image validation. **No live engine image changes occur on merge.**
3. Official container image refresh was evaluated without running a new service. Simply fetching currently published `postgres:16-alpine`, `redis:7-alpine`, `haproxy:3.2.23-alpine`, `clamav/clamav:1.4` does **not** resolve all fixable advisories: respectively **22, 4, 1, 1** HIGH/CRITICAL advisory records remained in the latest pulled tag at audit time. Most of these are Alpine/OpenSSL/pcre2 or Go stdlib issues that require upstream image rebuilds or a separately maintained derived image.
4. Postfix/Rspamd/Dovecot image scans reported Debian package advisories with **no fixed package versions recorded** by the scanner. Notably Rspamd's `libxml2` and Dovecot's `libmariadb3`/MariaDB metadata are linked to critical findings. Their actual reachability and runtime usage must be established separately. Do not remove libraries from a running mail engine or change the upstream Dovecot/Rspamd package set solely to make a scanner report green.
5. The development-only npm `braces` dependency (`@next/eslint-plugin-next -> fast-glob -> micromatch -> braces`) has [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm). **No upstream patched version is published** as of this audit. Upgrading `eslint-config-next` alone does not remove the chain. It is excluded from the Next.js production runtime but must remain visible in development/CI risk tracking. Do not downgrade the Next security release, silently suppress the advisory, or ship an unreviewed fork to hide it.

## Safe remediation / gate

- **Application first:** ensure SHA-pinned GHCR build publication is green; deploy the tested Django 5.2/Next.js 16 patched release through the established manual production gate with verified rollback/backup, tenant/auth/PostBox smoke checks, then inspect actual running image versions.
- **Native API/Olefy/policy separately:** after candidate CI passes, manually produce immutable reviewed GHCR image digests. Run DKIM-signing, mailbox creation/auth, service health and malware-filtering smoke checks before any production image cutover.
- **Stateful/native official images last:** track updated fixed packages in new official tags and pin digests after independent image scans and backup/restore testing. PostgreSQL volume updates, Redis learned mail-filter state and ClamAV signature state are sensitive; never run `compose down -v`, delete volumes, or execute a blanket rebuild on MateServer.
- **No patched upstream release:** record the exact package/CVE, investigate exploitability and existing isolation, and monitor for fixes; do not claim zero vulnerabilities or introduce hidden bypasses. Database, frontend, mail-engine, and development-tool security are separate scopes.

## Evidence retained

The per-image Trivy reports were generated into an access-restricted temporary directory on MateServer, **not committed to Git** (to avoid exposing host inventory by accident). Official candidate image pulls only added image layers to the local cache and did not recreate any running container.
