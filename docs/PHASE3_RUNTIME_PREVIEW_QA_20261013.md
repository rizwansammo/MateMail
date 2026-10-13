# MateMail Phase 3 Runtime Preview QA

Date: 2026-10-13
Branch: `feat/platform-domain-ownership-phase2-20261012`

## Implemented

The Platform Admin system mail readiness endpoint now provides:

1. Configuration alignment for current transactional sender, DMARC receiver and
   TLS reporting receiver, classified against the legacy mail domain and the
   future `system.matemail.pro` domain. Categories are legacy, target, other,
   unset. This is **environment configuration**, not delivery evidence.
2. A read only Native Engine resource inventory and planned operations for
   the system domain and five planned mailbox roles. Labels are present,
   would_need_activation, would_need_provisioning, unknown.
3. Explicit `changes_applied: false`, `cutover_ready: false` and
   `activation_supported: false`, regardless of draft or discovered resources.
4. Existing Platform Admin permission checks, immutable draft revision and
   history support.

## Validation evidence

Validation used a disposable PostgreSQL 16 database in an isolated Docker
network and a **copied** backend source tree overlaid with the feature branch.
Neither production database nor production application image was modified.

- Phase 2 and 3 targeted Django test suite: **144 run, zero failures**.
- Extended platform, domain, PostBox Security and Tenant Isolation suite:
  **191 run, zero failures**.
- Django migration model check: **no changes detected**.
- Python compile check for changed code: passed.
- The new tests explicitly verify that engine inventory does not call
  provisioning or password mutation methods, and that missing/unreachable
  engines are reported as unknown.
- Test counts overlap and must not be added together.
- Phase 2 Native SQL and provisioning checks were already validated on
  disposable databases in the prior QA reports. This update added no engine
  mutation code.

## Release boundary and open design work

This remains a **draft and read only integration**. No native platform
provisioning endpoint, DNS validation action, sender switch or credential
management endpoint is active.

A controlled native platform management workflow is required before the
system mailbox roles can be provisioned. The legacy generic Native API secret
is shared with tenant provisioning; it must not be reused as an unrestricted
platform bypass. Phase 2 native database triggers intentionally reject
mutations of reserved domains and mailboxes. A platform exception must
preserve the database protection boundary with audited, narrowly scoped
operations and distinct authorization. This is not implemented and must not
be simulated by disabling triggers or changing the native database directly.

The full GitHub Actions CI workflow and production delivery smoke tests are
still pending the Release 1 gate. **Do not merge or deploy yet.**
