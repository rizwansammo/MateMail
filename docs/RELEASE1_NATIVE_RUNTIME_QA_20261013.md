# Release 1: Native Runtime Database Privileges and HTTP Integration QA

Date: 2026-10-13
Branch: `feat/platform-domain-ownership-phase2-20261012`
Release scope: Phase 2 platform domain protection plus Phase 3 system mail backend.
Status: **NOT DEPLOY READY**. No production changes or CI workflows were made.

## Native Engine privilege hardening

Migration `009_runtime_db_role.sql` creates a dedicated regular Native API
database principal, `engine_api_runtime`, as **NOLOGIN** by default. The
principal has no PostgreSQL SUPERUSER, CREATEDB, CREATEROLE, BYPASSRLS,
TRIGGER ownership, or right to execute the Platform staging function.

New Native configuration:

- `NATIVE_RUNTIME_DB_ENABLED=False` is the initial safe default.
- `NATIVE_RUNTIME_DB_PASSWORD` is empty until explicitly provisioned and
  must be strong and different from bootstrap and other service credentials.
- When enabled, Native HTTP read/write handlers and `/ready` use
  `engine_api_runtime`, without fallback to the bootstrap superuser if
  authentication fails.
- A distinct bootstrap connection is used at startup for migrations and
  account credential updates. This is not a claim that the entire API process
  has lost its bootstrap superuser secret; startup privilege separation remains
  a defense-in-depth improvement to plan separately.
- The two independent Platform Staging gates remain disabled by default.
- No keys or passwords have been written into Git.

## Verified on disposable PostgreSQL 16 and isolated Native HTTP server

The tests operated only on isolated containers with randomly generated QA
secrets and an internal Docker network. No production database, domains, DNS,
DKIM keys, containers or mailbox data were changed.

1. SQL migrations 001 through 009 completed successfully. An initially
   malformed delimiter in migration 009 was corrected and the final script
   was applied successfully.
2. Restricted runtime role was NOLOGIN, NOSUPERUSER and NOCREATEROLE
   by default.
3. In the disposable database, the role could INSERT a normal customer
   domain/mailbox and read its Postfix mailbox view.
4. Protected platform domain insertion was DENIED by the database trigger.
5. Disabling the protected trigger, executing `platform_stage_system_mail()`
   directly, and changing a PostgreSQL superuser role were DENIED.
6. Synthetic legacy `mail.matemail.pro` data (three mailboxes, one DKIM
   metadata record) and Postfix/Dovecot lookup results were unchanged by
   migrations 007 through 009: `1|3|1|3|3` before and after.
7. Native HTTP startup using schema 9 and dedicated DB credentials succeeded.
   Both restricted Native runtime and Platform staging principals could
   authenticate with their independent credentials.
8. Real HTTP integration against that isolated Native API passed:
   schema 9 readiness, ordinary secret denied on Platform route,
   missing Platform secret denied, dedicated Platform staging successful,
   repeat staging idempotent, ordinary tenant mutation of reserved system
   domain refused, ordinary customer domain provisioning successful,
   customer mailbox provisioning successful, and mailbox inventory successful.
   An initial run used the previous image's older provisioning module and
   returned HTTP 500 for the blocked domain. After overlaying the **actual
   Phase 2 branch provisioning module** and restarting only the disposable
   Native API container, the complete HTTP test passed.
9. Native runtime unit tests: 6/6 passed, including read and write handler
   connection routing, independent credentials and missing-secret fail closed.
10. Prior Phase 3 Native Platform staging tests: 8/8 passed; Django Platform
    staging permission/audit/timeout tests: 8/8 passed. The earlier extended
    Django security regression suite (191/191) predates runtime role changes.
11. Temporary Native and Django test containers, internal network,
    mounted scratch code, and randomly generated QA secrets were removed.

## Release blockers still open

- [ ] Run the Django-to-Native staging flow against both isolated API servers
      via a real HTTP request. A new isolated Django runner attempt was
      blocked by the remote execution safety mechanism; **not passed**.
- [ ] Rerun the latest unified backend, PostBox, tenant-isolation and Native
      provisioning regression suites with the exact final branch tree.
- [ ] Run all required Release 1 CI checks with deliberate budget limits.
      Avoid redundant GitHub Actions workflows and unnecessary image
      publication; approval for use of private runner minutes is needed.
- [ ] Rehearse rollback of new schema 009 with the currently deployed older
      Native API and confirm platform invariants and actual readiness.
- [ ] Confirm production snapshot and deploy image tags; build a versioned
      rollback plan that includes both Django and Native Engine.
- [ ] Require `NATIVE_RUNTIME_DB_ENABLED=True` with distinct credentials
      for runtime privilege hardening at planned deployment or explicitly
      track it as an unfulfilled security gate.
- [ ] Controlled deployment and live SMTP/IMAP/DKIM smoke tests require
      separate release authorization.

## Deployment decision

**Do not merge or deploy yet.** Phase 2 + 3 implementation has advanced but
not all release gates are complete. Phase 5 activation, DNS cutover and
mailbox credentials are outside Release 1 and must remain disabled.
