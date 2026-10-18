# Phase 7A: Organization 2FA and SMTP/IMAP Password

Status: IN PROGRESS. No production enforcement or mail transport changes.

## Audited facts
- Hub authenticates accounts.User. It already has optional TOTP and backup codes, but no organization Off/Encourage/Require policy.
- PostBox authenticates mailboxes.Mailbox independently. A Hub User and a PostBox Mailbox are different principals, even when email strings match.
- PostBox currently checks the entered mailbox password against Dovecot IMAP, then issues its own session. Webmail uses a privileged internal master identity after sign-in.
- Postfix port 587 delegates SASL to Dovecot. Public IMAPS port 993 shares that authentication system. SMTP port 25 receives mail without authenticating mailbox users.
- Dovecot's SQL password lookup currently returns a single regular mailbox hash per address. It does not support per-device credentials or MFA-dependent password selection.

## Mandatory security contract
1. PostBox users explicitly control SMTP/IMAP access. Disabling this feature blocks public mail-client authentication but NOT inbound delivery or PostBox web access.
2. With mailbox 2FA ON or enforced, normal mailbox password MUST fail on public IMAP and SMTP; only dedicated SMTP/IMAP Password credentials can work.
3. With mailbox 2FA OFF and not enforced, normal mailbox password works only when SMTP/IMAP is enabled.
4. SMTP/IMAP Passwords are independently generated, one-time shown, hashed, labeled, individually revocable, and auditable without secret disclosure.
5. PostBox requires its own mailbox-bound 2FA and secure recovery. Never infer mailbox ownership or 2FA status from a Hub account by matching email.
6. Never block existing customer SMTP/IMAP clients simply because a new schema has been deployed. Migrate with explicit user action or controlled enforcement.
7. Postfix submission and Dovecot IMAPS must make the SAME server-side decision. Neither PostBox UI nor Hub authorization alone is an authentication guard.
8. Platform system mailboxes, provisioner and IMAP master identity remain separately protected; no customer self-service over them.
9. No secret or MFA recovery path can bypass organization Require policy.

## Initial backend work in this branch
- Tenant two_factor_policy field, default off, safe additive migration.
- Scoped Policy API for organization admins, guarded by independent feature flag and password reauthentication.
- Require cannot be activated even if flags are accidentally toggled; enforcement is not yet installed.
- Tenant-isolation, password, role and fail-closed regression tests.
- No modification to live Dovecot, Postfix, databases or DNS.

## Outstanding before Stage 7A can be called complete
A. Hub login/refresh/invitation/recovery security across all 2FA modes, with enrollment-only sessions and no inherited-session bypass.
B. Mailbox-specific 2FA and a distinct trusted primary-password verification endpoint for PostBox. Current direct IMAP sign-in must be replaced for MFA mailboxes or it would break PostBox.
C. Native Engine hashed per-device credential storage and Dovecot public passdb enforcement; SMTP/IMAP disabled and revoked devices must fail immediately on new auth. Avoid multi-row password lookups that can return ambiguous results.
D. Tests for TOTP, backup/recovery, revocation, retries, existing sessions, SMTP/IMAP, tenant isolation and platform system identities.
E. Safe legacy-client migration, feature flag rollout, rollback and independent review.

## Release
No merge, CI runs or production deployment before code review and explicit approval. This document and initial policy API are only the first Stage 7A checkpoint.


## 18 October 2026 — local development verification

Implemented in this continuation:
- Native Engine additive schema v013 stores per-mailbox remote access policy and separately hashed, labeled, individually revocable client credentials.
- Both native password tables are denied direct SELECT privileges for Postfix and Dovecot database readers. **No change to the existing public authentication view** and no customer self-service route yet.
- PostBox mailbox-bound 2FA model, encrypted authenticator secret and one-time high-entropy recovery codes. Uses a separately configured Fernet encryption key; missing or rotated keys fail closed.
- PostBox TOTP replay protection via one-time claim. Enrollment remains deliberately **unavailable** until public SMTP/IMAP enforcement and trusted web authentication are ready.
- All new schema changes are additive; no Production migration or public policy cutover.

Verification:
- 11 organization-policy/mail-client-schema Django tests passed in an isolated throwaway PostgreSQL database.
- 7 additional PostBox MFA cryptography/recovery tests passed against the same isolated test facility.
- Native SQL migrations 001 through 013 applied successfully on a standalone temporary PostgreSQL instance.
- Native credential tables deny SELECT to engine_ro_dovecot and engine_ro_postfix; legacy dovecot_auth remains unchanged.

Explicitly NOT completed:
- Dovecot public multi-password verifier and normal-password lockout for 2FA users.
- Trusted PostBox primary password verification to separate webmail authentication from public IMAP authentication.
- End-to-end organization Require 2FA enforcement, recovery, active sessions and Hub enrollment.
- User-facing SMTP/IMAP Password create/list/revoke, PostBox 2FA enrollment and production rollout.
- Production CI and real mail client acceptance tests.

**Do not set feature flags to ON or deploy the database-only foundation as a working SMTP/IMAP security feature.**
