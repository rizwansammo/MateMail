# Phase 3 Native System Mail Staging: QA and Release Boundary

Date: 2026-10-13
Status: Feature branch only, **NOT DEPLOYED**.

## Implementation

- Native migration `008_platform_offline_staging.sql` creates
  `engine_platform_operator` with **NOLOGIN** by default. This principal is
  `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, and has no
  direct INSERT/UPDATE permission on mailbox, domain or DKIM tables.
- Only this principal can call `platform_stage_system_mail()`, a
  fixed-payload PostgreSQL SECURITY DEFINER function with schema-qualified
  references and a restricted search path.
- It may stage **only** `system.matemail.pro` and exactly five inactive,
  login-disabled, passwordless roles: noreply, security, operations, dmarc,
  tlsrpt. It does not create DKIM, send messages, activate delivery, change
  legacy `mail.matemail.pro`, rotate credentials or move Maildir.
- A special trigger condition permits only these strictly scoped inactive
  INSERTs. All other protected-domain writes remain forbidden, including
  ordinary tenant and general Native Engine requests.
- Native API route `POST /v1/platform/system/stage` has an independent
  header/secret and gate `NATIVE_PLATFORM_OPERATIONS_ENABLED=False`
  by default. It calls the PostgreSQL function using the separate role, not
  the normal engine database connection.
- The Platform Admin route `POST /api/platform/system-mail/stage/` requires
  platform admin authentication, exact confirmation and draft revision,
  plus a separate gate `SYSTEM_MAIL_STAGING_ENABLED=False` by default.
  Its upstream Native API credential is distinct from the tenant provisioning
  credential. Pre-operation audit and confirmed/unknown outcome events are
  recorded; an ambiguous timeout does not falsely claim the operation failed
  without side effects.
- These routes are not wired to any automatic worker, scheduler or migration
  cutover. DNS, DKIM publication, mailbox passwords and live sender selection
  remain out of scope.

## Disposable database verification

All SQL tests used disposable PostgreSQL 16 containers and databases, never
production credentials or live storage.

- Native SQL migrations 001 through 008: PASSED.
- Migrated role default state: `NOLOGIN`, not superuser: PASSED.
- Native role granted execute on only the narrow staging function and no direct
  domain/mailbox table write: PASSED.
- Normal database principal calling the dedicated function: DENIED.
- Temporarily enabling the dedicated role on the disposable cluster and
  staging the fixed domain/roles: PASSED.
- Repeated function invocation: PASSED, one domain and five inactive,
  login-disabled, passwordless mailboxes; no DKIM row.
- In a separate disposable database, an existing mailbox address under an
  unexpected customer domain caused an explicit refusal. No partial target
  domain remained after the failed operation: PASSED.
- Corrected a SQL search-path / trigger lookup mismatch found during isolated
  tests. Explicit `public.` references were applied and verified by rerunning
  the stage function.

## API regression

- Native Python authorization, credential separation, exact payload handling,
  and route isolation: **8/8** passed.
- Django Platform Admin security, audit, confirmation, revision, unsafe result,
  and timeout handling: **8/8** passed.
- Previous Phase 2+3 read-only integration, PostBox security, domain ownership,
  Platform authentication, tenant isolation: **191/191** passed before the
  staging additions. This broader suite MUST be rerun for the final Release 1
  candidate; the counts must not be treated as one cumulative CI result.
- No GitHub Actions workflow runs were intentionally triggered.

## Critical limitations / release requirements

1. **Both staging feature gates remain OFF**. Separate strong secrets must be
   provisioned in the Native Engine and Django backend if staging is enabled
   later. The Native API role remains NOLOGIN while its gate is OFF.
2. The ordinary Native API database connection still authenticates as the
   historical `engine` PostgreSQL SUPERUSER. The new staging path has a
   dedicated unprivileged principal, but the service process itself is not
   completely isolated from its older superuser credential. A database writer
   role hardening plan is still needed for defense in depth; do not claim that
   the whole Native API service has lost superuser power.
3. No end-to-end HTTP staging integration with live credentials, no full CI
   matrix, no post-deployment SMTP/IMAP/DKIM smoke test yet.
4. This stage cannot activate messages or mailboxes. That transition requires
   separate, authorized, auditable Phase 5 work and validated DNS.
5. Do not merge, deploy, or activate any credentials without explicit
   authorization, a fresh backup and a tested rollback.
