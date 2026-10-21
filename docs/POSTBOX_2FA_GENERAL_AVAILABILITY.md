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

## Why Require is still blocked

The current Native `mail_client_policy` applies to each mailbox, not each tenant, and a missing policy permits legacy SMTP/IMAP credentials. Switching only Django's organization policy to Require would demand OTP in PostBox while **unenrolled mailboxes could still authenticate to SMTP/IMAP with the primary password**. That is not sufficient enforcement and must not be released.

The current Native API rejects `dedicated_required=true, enabled=true` until an active app password exists. A safe Require rollout must therefore support a lifecycle for *unenrolled* identities in which legacy external authentication is blocked until MFA enrollment and dedicated credentials are ready.

The production-grade Require path needs:

1. An atomic or durably recoverable tenant-scoped Native client-policy transition for every existing mailbox (including service failures and rollback), with positive enforcement acknowledgment **before** declaring Require effective.
2. New-mailbox, TeamBox, provisioning/reconciliation and changed-domain paths that cannot open a legacy SMTP/IMAP bypass before their security policy is installed.
3. Clear administrator warnings about client migration, session invalidation and how external applications regain access after enrollment.
4. Tests across Hub login/refresh/API keys, PostBox first-login enrollment/recovery/disable, Native IMAPS/SMTP acceptance, non-Require tenants, and failure/rollback.
5. Restore/recovery and persistent independent encryption-key verification before unlocking `can_activate_require()`. Merely setting `ORGANIZATION_2FA_ENFORCEMENT_READY=True` must never bypass it.

**Do not** change `can_activate_require()` to simply return True or toggle Require flags in production until these gates have passed.

## General availability readiness checklist

- Exact candidate commit: CI, security tests, Django check/migrations, Next build and browser QA green.
- Existing Native runtime capability and PostBox encryption key continuity verified without printing key material.
- Unenrolled mailboxes: unchanged web login, external SMTP/IMAP and email routing verified; enrolled mailboxes: trusted Native verification, OTP/recovery, app-password issuance/revocation.
- Canary ON with empty/invalid IDs selects nobody, and removing an already enrolled mailbox from the allowlist never removes MFA.
- Production activation occurs through the normal manual SHA-pinned deployment and explicit operator approval. Preserve a reversible feature config backup. **Do not disable both MFA flags after users enroll**, since recovery of protected identities must remain possible.
- Per-organization settings must never affect a different tenant.
- No broad authentication/organization policy flag changes are part of this candidate branch.

Next engineering task: implement and test durable Native tenant Require policy enforcement before enabling the Organization Require UI.
