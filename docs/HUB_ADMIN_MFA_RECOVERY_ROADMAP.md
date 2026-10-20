# MateMail Hub Administrator MFA and Owner Recovery Roadmap

Status: Phase 1 foundation in review. No activation or Production changes.

## Explicit scope
- Applies only to Hub workspace identities with roles Owner or Admin.
- PostBox mailbox 2FA, organization-wide 2FA, Native SMTP/IMAP and Platform Console sign-in are unchanged.
- Authenticator secrets and backup codes are never visible to workspace owners or platform operators.
- Every new mutating endpoint must be server authorized, tenant scoped, rate limited, audited, and covered by negative security tests.

## Five implementation phases

### Phase 1: Non-enforcing model and security boundaries
- Add `TenantMembership.admin_hub_mfa_policy` with Off, Optional, Required and default Off.
- Add separate Hub-only feature and enforcement readiness flags, both default false.
- Protect the actual `Tenant.owner` as mandatory **configured** Required when the new system is deliberately activated. A role string alone cannot claim owner authority.
- The real workspace owner, not a peer administrator, will be the only authority allowed to change another active admin's policy.
- This stage adds no login trigger, API or UI. Existing sign-ins must remain identical.

### Phase 2: Hub administrator policy and enrollment
- Build an owner-only, bound-workspace policy API and integrate it into Hub Users & access. Require owner's recent MFA and password confirmation to change policies.
- Off: no prompt; user can manually enroll on Account Security. Optional: non-blocking setup prompt with Skip. Required: password-verified partial challenge, mandatory TOTP setup and one-time backup code disclosure.
- Apply the effective policy to login, JWT issuance, refresh and protected API access. An existing access token must not bypass newly required enrollment; terminate/expire existing sessions as designed.
- Do not unlock the separate legacy organization-wide Require gate for this feature.
- Keep activation disabled until new and existing account recovery paths are verified.

### Phase 3: Administrator recovery initiated by the owner
- Personal backup codes and independent verified recovery email remain the first choice.
- Owner sees status, not OTP secrets or codes. Admin reset requires a recorded case, verified target identity, fresh owner authentication, cooldown and owner notification.
- A reset request is distinct from authorization to access the admin's account. No operator-issued password, raw recovery token or reusable bypass.
- Revoke the targeted account's existing refresh sessions and guard short-lived access tokens before enrollment access is allowed. Required remains Required after reset.
- Test cross-tenant, cross-role, peer-admin and forged-owner denial.

### Phase 4: Platform Owner Emergency Recovery
- Preserve existing endpoints: Owner metadata, emailed one-use Password Reset Link and session revocation; do not duplicate their logic.
- Add a verified Owner 2FA recovery and Full Recovery workflow (password and MFA) for the **actual** `Tenant.owner` only.
- Human identity and legal organization ownership verification, independent approval by separate authorized operators, request hold where appropriate, alerts and irreversible audit records must precede sensitive reset actions.
- A platform operator may start recovery but cannot learn/reset an owner's password or take over an account with a raw token.
- Reset TOTP and all backup codes only after approval. Revoke tokens; force password change where requested and mandatory new TOTP enrollment. Never silently downgrade Required.
- Do not implement a self-service Platform Super Admin recovery override; its break-glass access remains independent and requires its own separately approved design.

### Phase 5: Regression, evidence, controlled release
- Backend permissions, rate limits, challenge replay, stale-session API acceptance, email delivery failures, cross-tenant targeting and rollback tests.
- Hub responsive browser tests for optional and required login, code disclosure and safe recoveries.
- CI and GHCR image build, release manifest, flags OFF by default. Production feature activation requires explicit user approval and manual deployment.
- User performs production acceptance without sharing any OTP, TOTP secret, password or backup code.

## Existing implementation confirmed in the repository
- `backend/apps/accounts/views.py`: Hub TOTP, backup codes, OTP challenge and verified login.
- `backend/apps/accounts/recovery_views.py`: independent verified recovery email fallback.
- `backend/apps/platform_admin/owner_views.py`: operator initiated email password reset, metadata and session revocation with auditing.
- `backend/apps/tenants/security_policy.py`: existing organization-wide Require is intentionally blocked via `can_activate_require()`; it must remain separate.

## Mandatory security acceptance conditions
1. An admin cannot reset another admin or the owner, even with a manipulated request.
2. Owner recovery in the Platform Console cannot target a member or another tenant's owner.
3. The operator cannot obtain or set the recovered person's password, authenticator seed, backup codes or final login session.
4. Required 2FA cannot be skipped via login, invite acceptance, refresh, stale JWT, recovery or reset.
5. Existing Hub login, PostBox and SMTP/IMAP remain unchanged until their distinct release gates explicitly authorize a change.
6. A completed reset must invalidate every old recovery code and TOTP secret, with race and retry tests.
7. Separate Platform Super Admin emergency access must not be assumed to exist merely because Workspace Owner recovery exists.
