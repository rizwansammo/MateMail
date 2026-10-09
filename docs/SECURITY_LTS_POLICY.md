# MateMail — Supported Dependency and Security Policy

Effective: 2026-10-09. Covers the Django/Next.js application stack; the separately built Native Mail Engine and OS/container images require their own image-level scans and controlled release process.

## Principles

- Use upstream **LTS** release lines when provided; otherwise use a **currently security-supported stable** release. No unsupported runtime or framework release belongs in production.
- Patch within the supported release line promptly when upstream publishes a relevant security fix. A newer major version is **not** a goal by itself.
- Track upstream EOL dates and plan migrations **3–6 months before** the end of security support.
- Production dependency pins and npm lockfile must be reproducible. Changes use a GitHub PR and receive CI, migration, authentication, tenant-isolation, DKIM/mail-flow and rollback reviews as appropriate.
- The CI pipeline blocks on `pip-audit --strict` for the resolved Python backend/test environment, `pip check` and `npm audit --omit=dev --audit-level=moderate` for the frontend production graph. Do **not** waive findings with `|| true`, `--ignore-vuln` or an insecure package downgrade to turn CI green.
- Production deployment is a **separate manual workflow**, not an automatic effect of merging a PR. Verify the central Azure DR backup and local Restic restore path before any schema migration or application cutover. Keep the previous images, original .env and compose files available for rollback.
- No debug mode, hardcoded production secrets, permissive origin authentication, credential bypasses or backdoor accounts are acceptable. Security tooling is one layer, not proof that the application has zero vulnerabilities.

## Current target release lines (2026-10-09)

| Runtime/component | Release policy |
| --- | --- |
| Django | 5.2 LTS, security-supported until April 2028; currently patched at 5.2.18 |
| Next.js | 16 Active LTS; currently patched at 16.3.8 |
| Node.js | 24 LTS |
| Python | 3.11 still security-supported; plan a managed upgrade before its October 2027 end-of-life |
| PostgreSQL | 16 still supported, keep it on current security patches |
| Other Python/npm packages | Supported compatible patched versions with CI audits |

### Scope of P3

The application and backend/frontend dependency set is addressed first, with tests and CI audits before a cutover. The **Native Engine** (Postfix, Dovecot, Rspamd, ClamAV, Native API and separate images) and OS images must be audited and remediated separately to avoid disrupting customer email delivery. A clean app dependency scan **does not** certify engine images or infrastructure.

Known pending *build-only* advisory: the Next.js ESLint dev-tool chain currently resolves `braces@3.0.3`, which has an upstream denial-of-service advisory with no published patched `braces` 3.x release as of this audit. It is **not** in the production npm dependency graph (production audit passes). Track upstream for a compatible security fix; do not downgrade `eslint-config-next` to an incompatible major version or call it resolved.

## Release evidence

For each security upgrade, retain the reviewed PR, GitHub CI results, `pip-audit` / `npm audit` reports, validated schema migration plan, fresh backup, image tags and post-deployment smoke-check results. If any gate fails, stop; do not weaken the test or hide the finding.
