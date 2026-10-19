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


## 2026-10-18 — Stage 7A authentication integration checkpoint

### Implemented (on feature branch, NOT deployed)
- Native Engine SQL migration 014: password-dependent, server-side Dovecot verifier; independently hashed per-device credentials; legacy-password denial when dedicated access required; disabled-protocol denial; revoked-secret denial; protected least-privilege DB grants.
- Dovecot passdb candidate now uses only the verifier function. Must verify with pinned Dovecot 2.4 in a full isolated authentication integration test before deployment.
- Native internal API: trusted primary-password verification, dedicated password issue/list/revoke and remote-access policy; separate OFF-by-default feature gates. New passwords are returned only once; server retains a bcrypt hash.
- Hub: second-factor-proven JWT claim, enforcement checks on live tenant access and refresh, enrollment-only challenge path and protection against disabling MFA while Require is effective.
- PostBox: separate trusted primary-password authentication from public IMAP, mailbox-bound OTP challenge and authenticated sessions, MFA enrollment/verify, initial client-password issuance after OTP proof, client password management, OTP-protected disabling and live-session proof checks.

### Verified in isolated environments
- Native migrations 001–014 applied successfully to a disposable PostgreSQL instance.
- Native SQL verification for 2FA-required legacy password denial, dedicated password success and revocation, disabled external clients, no credential-table access for Dovecot/Postfix.
- Native Client Credential Service: 7/7 tests passing.
- Hub and PostBox selected regression suites: 71/71 tests passing, including authentication security and tenant isolation.

### Outstanding security gates — DO NOT ACTIVATE
1. Hub TOTP is still stored in plaintext in the legacy 2FA table. Add encryption and tested data migration without locking out existing TOTP users.
2. Verified recovery email and independent Email OTP; missing account recovery anti-lockout guarantees. Organization Require cannot be considered production ready.
3. New dedicated client password revocation must terminate or invalidate already authenticated IMAP sessions where feasible. Only subsequent password auth rejection is currently tested.
4. Dovecot 2.4 real SMTP AUTH/IMAP authentication test with malformed credentials, master-user isolation, sender authorization and existing client compatibility.
5. Recover and coordinate partial failures between Native policy updates and Django 2FA activation (cross-database saga).
6. Migration/feature-flag sequencing on Native API, Dovecot and PostBox, and explicit production rollback rehearsal.
7. PostBox/Hub UI is intentionally Stage 7B; rollout/production acceptance Stage 7C.

**Status: Stage 7A IN PROGRESS — not safe to merge or deploy as a completed MFA release.**

## 2026-10-18 — PostBox and Hub hardening continuation

New additions:
- Hub TOTP encryption: `accounts.0004_encrypt_existing_totp_secrets` encrypts existing plaintext Hub TOTP seeds in a transactional data migration. `HUB_2FA_FERNET_KEY` is independent of SMTP/IMAP secrets; missing or wrong key fails closed. `accounts.0005_verified_recovery_email` adds verified independent recovery email and one-time recovery login challenge state.
- Hub 2FA disable requires current password AND a valid, non-replayed TOTP or unused backup code. Existing TOTP provisioning seed is no longer returned by an authenticated GET after enrollment.
- Recovery email verification requires recent primary reauthentication and existing TOTP where already enabled; recovery OTP is tied to an independently verified external address, password-proven partial challenge, and one-time consumption. Live tenant membership is checked *before* second-factor consumption.
- PostBox MFA disable now revokes dedicated credentials first, modifies Django MFA state second, and relaxes Native client policy last, preventing a partial failure from re-enabling the normal mailbox password while PostBox still considers MFA active.
- Native v014 and encrypted Hub migrations remain **NOT deployed**. Newly introduced keys must be provisioned only at later controlled activation and retained by existing Azure Disaster Recovery configuration.

Evidence recorded:
- Existing Hub 2FA + account hardening + JWT refresh + Connected Apps + new PostBox MFA: 72 / 72 isolated Django tests passing (2026-10-18).
- Recovery email verification / OTP / single-use: 3 / 3 isolated tests passing (2026-10-18).
- Native Client Credential API unit tests: 7 / 7 passed on pinned Native API container (earlier development checkpoint).
- Native SQL migrations 001–014 and SQL auth/permissions tests passed using disposable PostgreSQL. An additional Dovecot 2.4 runtime acceptance harness was attempted but tool safety restrictions prevented starting its credential-bearing test container.

Remaining release blockers:
1. **Real Dovecot authentication acceptance** using the exact pinned Dovecot 2.4 runtime, checking its `%{password}` SQL passdb expansion and public IMAP/SMTP parity, master-user separation and old client handling.
2. **Revoked credential active-session invalidation** (e.g. verified `doveadm kick`) and fail-closed behavior during administrative API outages. Only new authentications are proven to reject revoked credentials.
3. **Cross-database transition/recovery and rollback rehearsal**, especially Native DB and Django DB partial failure, OTP encryption key continuity and pre-existing mailboxes.
4. Comprehensive integration CI / external mail-client regression and production security acceptance in subsequent 7C. Complete production activation MUST stay OFF.

**Stage 7A has meaningful implementation and passing tests but is NOT closed until the first three blockers have verified evidence. Do not merge or deploy as a finished feature.**

## Stage 7A closing gate review — 2026-10-18

Additional implementation:
- Native credential revoke / SMTP-IMAP access policy mutation commits the database restriction *before* asking Dovecot's admin API to kick current IMAP sessions. New auth is therefore blocked even when doveadm is unavailable. A failed kick returns 503 with an explicit reconciliation warning, never a false success.
- Doveadm command is scoped to an exact validated mailbox address with wildcard rejection. A call to the documented `kick` HTTP API requests `force=true` and checks its acknowledgement.
- Hub 2FA disable requires the existing second factor and no longer resets the brute-force counter merely because the primary password was right. Existing authenticator secret cannot be extracted through the setup route.
- Added Native session-revocation ordering/outage regression tests, and a targeted legacy TOTP encryption migration rehearsal test.

Verified (not deployed):
- Pinned Dovecot **2.4.5** parses the isolated new SQL password-dependent passdb configuration with `%{user}` and `%{password}` successfully, via `doveconf -c ... -n`. This checks syntax, **not** a real IMAP/SMTP login.
- Native Dovecot kick unit suite: **7/7 passing**, including per-mailbox mask validation, commit-before-kick and outage response path.
- Previous full Hub/PostBox authentication regression: **72/72 passing**.
- Previous independent recovery email suite: **3/3 passing**.
- Previous native SQL credentials and least-privilege tests passing; v001–014 migrations applied in disposable Postgres.
- The migration-rehearsal test is authored but has **not executed**, because the execution tool blocked the isolated test run; do not claim the migration rehearsed.
- Branch code saved without touching main or deploying. No production client or tenant changes.

**Stage 7A is NOT yet formally closed.** These are final release blockers, and none can be implied from the unit tests above:
1. Real Dovecot IMAP/SMTP AUTH end-to-end with isolated, synthetic mailboxes, plus PostBox master login isolation and reconnect tests. Prior attempted credential-bearing isolated Dovecot launch was denied by tooling; DO NOT use live customer mailboxes as workaround.
2. Actual active IMAP session revocation under `doveadm kick`, including admin API failure and retry, and SMTP AUTH session handling. The implementation and mocks alone do not establish a runtime guarantee.
3. Safely replay older plaintext Hub TOTP records through encryption migration and rollback with protected cryptographic key continuity; the rehearsal test has not run.
4. Controlled migration/saga failure recovery across Django and Native databases, full regression and release evidence.

All Stage 7A feature flags and Native credential handlers must remain OFF in production until those gates pass. Real production activation belongs to Stage 7C. No workarounds weakening 2FA to claim completion.


## 2026-10-18 — Stage 7A isolated runtime acceptance

Isolated acceptance in /opt/TestLab/MateMail-Stage7A completed without touching production. See TestLab/results/PHASE7A_ACCEPTANCE_2026-10-18.md for detailed test evidence.

- Real Dovecot IMAP password acceptance/rejection: 13/13; Native bcrypt $2b$ compatibility: 3/3 additional IMAP cases.
- Real Postfix 587 SMTP AUTH against Dovecot socket: 9/9; Native bcrypt $2b$ compatibility: 3/3 additional SMTP cases.
- PostBox master identity authentication/isolation: 4/4.
- Adversarial IMAP/SMTP authentication inputs: 8/8.
- Real doveadm CLI and HTTP admin API kicked only the intended active IMAP session; another user session survived.
- Critical HTTP API compatibility bug fixed: Dovecot 2.4.5 requires an **array** in kick.mask, and rejects the unsupported force argument. The corrected call returned doveadmResponse count=1.
- Encrypted Hub TOTP reversible migration now decrypts only during an explicitly requested rollback, before reverting the database field length. Legacy->encrypted->legacy->encrypted roundtrip preserved OTP exactly (1/1).
- Combined Django regression 93/93, Native credential/revocation unit tests 14/14, Native SQL fixture PASS; Django migrations consistency PASS.

**Stage 7A development and isolated acceptance: verified.** The branch must remain isolated with feature gates OFF. Stage 7B will provide Hub/PostBox controls; Stage 7C must verify public STARTTLS/IMAPS, production-key provisioning, canary compatibility and rollback, and receive explicit production deployment approval. No merge or production deployment is part of this isolated acceptance.
