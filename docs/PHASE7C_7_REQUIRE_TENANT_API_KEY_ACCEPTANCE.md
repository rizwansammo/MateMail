# Phase 7C-7 — Require MFA Tenant API-Key and PostBox Enforcement

**Date:** 2026-10-19  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Status:** Isolated Django authorization and forced-enrollment tests PASS. Require activation stays LOCKED.

## Finding: revoked membership still used a tenant API key

The tenant middleware previously cached an `mm_` key's creator and attached its
tenant to the request **before** validating that the creator still had ACTIVE
membership of that tenant. When the membership check failed it silently ignored
the failure. The DRF `HasTenantAccess` permission checked only that a
request had a tenant, not a confirmed membership.

An isolated real Django HTTP negative regression test first proved that an API
key issued to a formerly active owner could still fetch `/api/billing/`
with HTTP **200** after the creator's membership had been REMOVED. This is a
real tenant-data confidentiality problem beyond the Require MFA flag.

## Fixes

- `TenantMiddleware` now refuses an otherwise valid API key with HTTP 403
  when its creator account is disabled, deleted or no longer has ACTIVE
  membership in the key's *own* tenant. No tenant context or authenticated
  key cache is populated until membership validation succeeds.
- DRF `HasTenantAccess` now requires membership for the exact tenant and
  requesting user in addition to authentication. This is an independent
  defense for billing and DNS read-only views.
- The prior Require MFA gate remains: keys cannot substitute for interactive
  MFA evidence when that tenant's effective policy is `require`.
- Existing key scope restrictions, platform/internal route denials and
  valid workspace API key behavior are preserved.

**Compatibility consequence:** when an API key creator leaves a workspace or
their account is disabled, automations using that key stop working. Operators
should reissue any needed machine keys under an appropriately authorized
active workspace account. No keys are rotated or revoked in production here.

## Tests and evidence

- Negative test before the fix: `/api/billing/` returned **200** after key
  creator membership was revoked, whereas 401/403 was required.
- Tenant API key + Require MFA + policy + refresh + real Django HTTP / partial
  error contract: **92/92 PASS** after fix; 3 added API key membership/creator
  status tests. Log: `results/stage7c-key-membership-green.log`.
- PostBox Require MFA forced-enrollment acceptance + organization Require,
  key scopes and policy: **65/65 PASS** after three added forced-enrollment
  tests. It covers password-only session denial, pre-policy browser session
  denial, enforced enrollment only after mocked Native policy confirmation,
  disabling MFA forbidden while Require is active, and fail-closed behavior
  when the Native adapter is unavailable. Log:
  `results/stage7c-postbox-require-boundary.log`.
- Real Django DB/Redis and HTTP middleware were used. Native adapter calls in
  these PostBox enrollment tests were **mocked**; these tests are **not**
  live Dovecot/SMTP client acceptance evidence.
- Enforced Require was activated **only** inside Django test overrides.
  Production flags, live mail accounts and `main` remain unchanged.

## Still open before deployment

- Complete isolated live Native-issued password policy/revoke/real Dovecot
  already-established IMAP session disconnection and worker outage/recovery.
- Release readiness for Require MFA across all external SMTP/IMAP client
  behavior, key continuity and recovery paths; the
  `can_activate_require() -> False` barrier has NOT been bypassed.
- CI, PR review, production backups/restore, approved canary and rollback.
- Keep all synthetic TestLab scripts/logs/files until Phase 8 is finished.
