# PostBox 2FA general availability and organization-controlled policy

Status: **implementation in progress**. Do not activate production-wide MFA or Organization Require with this document alone.

## Product ownership

MateMail makes encrypted TOTP, ten one-use recovery codes and dedicated SMTP/IMAP client passwords available. An organization owner or administrator chooses its organization-wide policy; the platform operator does not silently set organizations to Require.

Existing persisted organization policy values:

- `off`: no organization-wide requirement; individuals can still voluntarily enable 2FA. Previously enrolled identities must stay protected.
- `encourage`: 2FA enabled/recommended but optional. Individuals choose whether to enroll.
- `require`: the organization requires 2FA for its Hub and PostBox identities. Only effective after the independent server-side release gate has been accepted.

Organization-scoped Hub MFA is distinct from the independent admin-only Hub policy; mailbox PostBox MFA is tied to Mailbox identity, not a Hub User.

## Compatibility invariants implemented in this candidate

- Normal **unenrolled** PostBox identities continue using the existing IMAP primary-password verifier. Global feature availability must not switch all current users to Native web-password verification at once.
- A **canary-selected** mailbox, a previously **enrolled** mailbox, or an effectively **Required** mailbox authenticates its primary password using the trusted Native API and never falls back to public IMAP when Native is unavailable.
- Native enforces dedicated SMTP/IMAP passwords on **enrolled** mailboxes; their app passwords cannot log into PostBox web.
- An existing enrollment remains protected even if an organization policy is changed to Off, the allowlist changes, or both platform MFA flags are turned off by mistake. A rollback without a safe recovery procedure must fail closed, not turn SMTP app-password knowledge into a password-only web session.
- The existing optional 2FA UI, recovery code rotation, revocation and automatic logout retain their existing protections.
- The organization policy API remains owner/admin-only and requires fresh Hub password reauthentication.

## Native tenant protocol enforcement candidate

The draft PR adds **additive Native migration 016**. Its `domain.tenant_id`
binding is immutable across different tenants, while `native_tenant_mfa_policy`
stores a durable tenant-level Required bit (default False). Dovecot's existing
`dovecot_client_auth` function checks this bit **before** accepting a mailbox's
primary password. Every mailbox belonging to the bound domain, including new
mailboxes created later, inherits the same security rule.

The authenticated internal Native API route `POST /v1/tenants/mfa/require`:
- strictly validates tenant UUID, bounded domain list and boolean policy;
- rejects cross-tenant ownership and reserved platform domains;
- updates domain-to-tenant bindings and the protocol enforcement row in one
  Native PostgreSQL transaction;
- avoids mass session disconnects when an unchanged Off/Enable policy is saved;
- retries only pending failed kicks when reapplying an already committed tenant policy;
- queues durable IMAP session disconnects in the SAME transaction before
  acknowledging the policy change, and reports an unverified disconnect as
  HTTP 503, not success;
- never exposes the internal mailbox identities in public Hub responses.

The Hub policy API enables Native Require *before* committing the Django
Require policy. Disabling Require commits Django's relaxed policy before
relaxing Native; a Native outage then leaves clients temporarily restricted
rather than leaving a security bypass. Repeating the same Off/Encourage update
reconciles a failed Native release.

### Explicit final release gate

The configured `ORGANIZATION_2FA_ENFORCEMENT_READY` flag is not sufficient.
The additional independent `ORGANIZATION_2FA_NATIVE_ENFORCEMENT_ACCEPTED`
flag defaults to **False**. `can_activate_require()` checks all participating
flags, Native adapter and separate valid Hub/PostBox Fernet keys before making
Require selectable by a customer. No tenant is changed automatically.

**DO NOT set the acceptance flag in production** until the real Native image,
migration, IMAP/SMTP session-disconnect acceptance and recovery runbook have
been explicitly verified. It is a deliberate manual release declaration, not a
way to bypass testing.

### CI acceptance in this draft

- Synthetic Native HTTP test: policy transaction commits after the durable kick
  outbox; outgoing kicks/acknowledgements occur after commit; failed kicks
  return 503 with pending jobs retained; invalid internal secret denied.
- Native unit tests: UUID validation, domain ownership, platform-domain denial,
  incomplete tenant domain set, empty tenant and new-domain persistence.
- **Real disposable PostgreSQL test** applies Native migrations 001–016 and
  calls the Dovecot authentication function for existing/new mailboxes,
  primary/dedicated passwords, cross-tenant isolation, revoke and rollback.
- Django synthetic tests cover the Native-before-Django Require ordering,
  downstream outage and idempotent release retry.
- The old canary manual IMAP/SMTP tests already accepted app-password behavior,
  but do not substitute for a *new* native tenant-Require protocol acceptance.

## Release order (manual, not executed by this PR)

1. Verify production backups and the actual installed Native/Dovecot image
   without exposing encryption keys. Prepare the recovery plan.
2. Build and deploy the updated **Native Engine** independently, with migration
   016, while the tenant-enforcement acceptance flag remains OFF. Confirm
   runtime `NATIVE_CLIENT_CREDENTIAL_API_ENABLED` and
   `NATIVE_CLIENT_KICK_RECOVERY_ENABLED` are enabled.
3. Deploy the SHA-pinned MateMail Backend and Frontend images with default
   feature flags still OFF. Verify healthy services and no changed SMTP/IMAP
   authentication for non-enrolled mailboxes.
4. After read-only config checks, activate the voluntary PostBox and
   organization policy features, while keeping
   `ORGANIZATION_2FA_ENFORCEMENT_READY=False` and
   `ORGANIZATION_2FA_NATIVE_ENFORCEMENT_ACCEPTED=False`.
   The tenant's persisted existing Off/Encourage policy remains unchanged.
5. On explicitly authorized synthetic test identities, verify the true
   Org Require state transition, IMAPS 993 and submission 587, fresh and
   established sessions, recovery and re-enrollment, plus an unenrolled
   second-tenant control account.
6. Only after acceptance and operator approval, set both independent Org
   Require release flags, confirm Django deployment checks, and make the
   Require option selectable. Customer owners/admins decide *whether* to
   enforce it; the platform does not choose for them.

### Rollback

- Do NOT revert to an older Native image while any tenant is Required: the
  database migration and persistent tenant policy must stay enforceable.
- Disable Require only through an authorized, positively acknowledged
  organization policy transition, not an unreviewed SQL edit.
- Keep PostBox 2FA and SMTP/IMAP security active for enrolled mailboxes;
  switching both flags OFF is fail-closed and can strand their recovery flow.
- If the Native engine cannot confirm session disconnect, do not call rollout
  completed; reconcile the durable kick queue, then retry the same operation.
- Never drop the Native tenant-policy table merely to reverse the feature.

## Remaining acceptance before GA

- Exact PR commit: Backend tests, Django checks/migrations, Next build and Native
  engine candidate tests must pass with no unreviewed failures.
- Native migration 016 on an isolated PostgreSQL database and all Dovecot
  protocol checks must pass.
- A separate controlled live IMAPS/SMTP **tenant Require** rehearsal and safe
  operator recovery/rollback must be signed off.
- New-domain provisioning, TeamBox, API key, Hub login/refresh, PostBox first
  login/enrollment, cross-tenant isolation and failure handling must be
  verified as part of that rehearsal.
- No customer organization policies or runtime security flags may be changed
  as a side effect of this draft PR.

Current state: **implementation candidate, not merged or deployed**.
