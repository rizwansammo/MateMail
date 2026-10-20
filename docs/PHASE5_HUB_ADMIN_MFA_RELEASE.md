# Phase 5 — Hub Administrator MFA Final Security Acceptance & Controlled Release

Status: GitHub implementation / automated verification first; **production remains separate**.

## Scope and invariants

This release concerns Hub/Workspace Owners and Administrators and separately
Platform security administrators helping a workspace owner. It does **not**
turn on 2FA for ordinary mailbox users or modify SMTP, IMAP, the Native Engine
or PostBox credential handling.

Four server-side gates remain **OFF by default**:
- `HUB_ADMIN_MFA_POLICY_ENABLED=false` — Owner-managed Admin Off/Optional/Required policy UI and API
- `HUB_ADMIN_MFA_RECOVERY_ENABLED=false` — Owner-assisted Admin authenticator reset
- `PLATFORM_OWNER_EMERGENCY_RECOVERY_ENABLED=false` — two-operator Owner emergency recovery
- `HUB_ADMIN_MFA_ENFORCEMENT_READY=false` — actually enforce Required for Owners/admins on login and existing Hub sessions

**Changing a feature gate requires a controlled restart/redeploy** with the
proper values in the runtime environment. Do not put passwords, TOTP seeds,
raw recovery codes or session tokens into GitHub variables, logs or tickets.

## Phase 5 verification findings

- Hub and Native Engine containers were healthy at read-only preflight.
- On October 20, production still ran image tag
  `7db48f776f4df638c2cefcecf31690125d44fb0d`,
  predating Phase 1–4 security merges.
- The production database preflight (aggregate only, no addresses) found:
  **2 active Platform operators**, **4 workspaces with owners**,
  **1 owner with TOTP**, **0 verified independent recovery emails**.
- Therefore REQUIRED enforcement is **NOT READY** and must not be activated.
- Phase 5 corrects access-token derivation on refresh: every new access token
  must inherit the *new* security epoch, and platform email OTP proof must
  survive normal refresh rotation without extending the 15-minute fresh proof.
- The new read-only readiness checker returns exit **2** while blocked and
  never logs individual email addresses or secret material.

## Automated release checks

1. Merge all Phase 5 security fixes only after full Django backend regression,
   frontend lint/build, Compose validation, runtime security smoke and any
   required visual QA complete successfully.
2. Confirm migrations are additive, and production env defaults keep all four
   gates OFF. Do not run a Django database migration against Production outside
   the documented deployment.
3. From the **new deployed backend**, execute the read-only guard:
   `python manage.py audit_hub_admin_mfa_readiness --json`
   (e.g. with the existing service's `docker compose exec -T backend`).
   Exit **2** means BLOCKED. Do not override; remediate.
4. Confirm **at least two distinct** currently active Platform security
   administrators are able to complete their own emailed Platform Console OTP
   login. Count alone is not proof of operational access.
5. Confirm every workspace Owner and every Administrator with a configured
   REQUIRED policy has an independent, accessible, verified recovery email.
   Hub → Account Security → Independent recovery email, with verification code
   sent to that external inbox. Do not store the verification code.
6. Confirm external transactional delivery using synthetic QA identities.
   Use a dedicated non-production test workspace for 2FA reset testing; do not
   reset a real Owner account for testing.
7. Record Docker image digests, database migration version, GHCR publish
   success, current healthy containers and rollback tag BEFORE activation.

## Controlled activation sequence (manual approval per stage)

### Stage A — Code deploy, all feature gates OFF

- GitHub Actions → Deploy to MateServer (main; exact merged image SHA).
- The operator manually confirms the workflow as usual.
- Verify frontend, backend, Celery worker and beat report healthy.
- Verify Hub and Platform Console HTTP and **old** login/2FA behavior.
- Run database `audit_hub_admin_mfa_readiness --json`.
- No new Admin MFA policy or Emergency Recovery is active yet.

### Stage B — Recovery contact enrollment

- Owners individually confirm an **independent** recovery inbox.
- Administrators that may be required confirm theirs independently too.
- Keep `HUB_ADMIN_MFA_ENFORCEMENT_READY=false`.
- Repeat the readiness command until `ready: true`.
- If coverage is missing, do **not** proceed to mandatory policy enforcement.
- Avoid telling users to share their authenticator OTP, password, backup code
  or verification email OTP with a platform operator.

### Stage C — Recovery gates, then optional policy

- Separately activate:
  `HUB_ADMIN_MFA_POLICY_ENABLED=true`,
  `HUB_ADMIN_MFA_RECOVERY_ENABLED=true`,
  `PLATFORM_OWNER_EMERGENCY_RECOVERY_ENABLED=true`.
- Keep `HUB_ADMIN_MFA_ENFORCEMENT_READY=false` until Stage D.
- QA test Optional admin: password login recommends setup, can Skip.
- QA test enrolled admin: always completes OTP challenge before entering Hub.
- QA test owner-only Admin policy and owner initiation of Admin Recovery,
  independent 30-minute email consent, one-time link, revoked old factors/JWTs
  and forced re-enrollment. Use QA accounts exclusively.
- Owner Emergency Case: first operator opens, second different operator
  approves, 24-hour hold is server-enforced, independent reviewer dispatches,
  owner confirms once from external address. Test cancelled/replayed links and
  alerts on QA accounts. **Full live 24-hour flow cannot be declared tested
  until that waiting period actually passes.**

### Stage D — Required MFA enforcement

- Re-run `audit_hub_admin_mfa_readiness --json`, save aggregate result.
- Verify all QA recoveries are successful and operators are reachable.
- With explicit release approval, enable
  `HUB_ADMIN_MFA_ENFORCEMENT_READY=true`.
- Sign in as unenrolled QA Owner: must set up TOTP *before* a Hub JWT is issued.
- Sign in as enrolled QA Owner: OTP required on every login.
- Reuse an old pre-enforcement password-only JWT and refresh cookie:
  server must refuse Hub access and refresh.
- An Owner may make Administrator policy Required only while gate is ready;
  non-Owners cannot change it or reset an Owner.
- Newly required Administrator must enroll before access and cannot self-disable.
- Verify backup codes are single-use; regeneration revokes all old codes.
- Verify application-generated API keys cannot bypass a required Hub OTP.
- Verify tenant hostname binding, audit trails and user alerts.
- Verify unrelated mailbox login, IMAP, SMTP and PostBox remain unaffected.

## Rollback

1. If production login locks a user out unexpectedly, **first disable
   `HUB_ADMIN_MFA_ENFORCEMENT_READY`** with a controlled runtime restart;
   retain the recovery feature and account security epochs.
2. If recovery endpoints themselves misbehave, independently disable the
   corresponding recovery feature flags. Preserve audit logs.
3. Roll back application image to the *previous validated deployed SHA* using
   the normal GitHub Actions rollback procedure, only after checking whether
   new migrations remain backward compatible. **Never blindly reverse a
   database migration that stores issued security cases or session epochs.**
4. MFA reset operations are intentionally irreversible. Do not restore deleted
   backup codes or reuse old TOTP seeds from backups.
5. Verify all four containers healthy and run authentication smoke tests again.

## Acceptance conditions / actual signoff

Code Merge + CI Green is **Engineering Acceptance**, not automatic Production
Acceptance. Production signoff requires verified production image, readiness
checker exit 0, independent contacts, explicit flag activation and a manual
real-world or isolated QA walkthrough of every crucial flow. The 24-hour hold
requires time and independent staff; it cannot be bypassed for convenience.
Document evidence, approver, rollout and rollback timestamps before closing.

If an Owner loses *both* the authenticator and verified independent recovery
email, automated recovery **fails closed**. An independently authorized,
documented offline identity process is required; this release deliberately
does not implement a self-serve operator bypass.
