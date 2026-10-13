# Native Engine Platform Resource Management Security Design Gate

## Existing boundaries

- Django organization provisioning and the existing Native Engine API share
  a standard service credential. That credential permits tenant operations
  but must NOT permit system mail overrides.
- PostgreSQL native migration 007 protects reserved domain and mailbox rows.
  It currently blocks all protected identity changes except login timestamp
  bookkeeping. Retain these protections until a production safe authorization
  design is validated.
- Django Phase 3 currently writes only an immutable, audited configuration
  draft; the read only integration uses existing Native Engine list calls.

## Minimum next implementation requirements

1. A dedicated platform operations authorization scope that is never
   interchangeable with the tenant provisioning or Dovecot policy secrets.
2. An exact allowlist of platform domain and system mailbox operations, with
   input addresses bound to `system.matemail.pro`. No arbitrary target domain
   or organization mailbox access.
3. Database enforcement that cannot be bypassed by a request carrying the
   ordinary Native API credential. A secret alone is not a database privilege
   boundary. Review the Native API database role before selecting the design.
4. Idempotent create/update and separate irreversibility safeguards for
   passwords, storage identity, DKIM rotation, and domain deletion.
5. No exposure of passwords, secret values, DKIM private keys, IMAP contents,
   or customer data to Platform Admin API responses, audit rows or logs.
6. Audit records and correlation IDs for all intended privileged operations,
   with safe failure and no silent mutation.
7. Engine and Django migration ordering, feature gates default OFF, tested
   rollbacks, and production shaped delivery/auth verification before release.
8. Deployment of the Phase 2 + Phase 3 package must not start reconciling the
   legacy system domain automatically.
9. Domain DNS cutover, sender/receiver activation and live delivery changes
   remain in Phase 5, not in this provisioning prerequisite.

## Decision

Do not implement a shortcut that deletes or disables platform protection
triggers. Further work starts with a read only review of the Native Engine
database role and the service credential topology, followed by isolated
tests of a narrowly privileged management interface.
