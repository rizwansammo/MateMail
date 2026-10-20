# Phase 4 — Platform Workspace Owner Emergency Recovery

Status: implementation subject to CI, independent review, manual deployment,
and Phase 5 production acceptance. Disabled by default.

## Existing actions (unchanged)

- Owner metadata: `GET /api/platform/tenants/<tenant>/owner/`.
- Password-only recovery: `POST .../owner/password-recovery/` mails the existing one-hour password reset link to the owner's **primary** email; it never sets an operator-known password. Existing owner 2FA remains unchanged.
- Session revocation: `POST .../owner/revoke-sessions/`; existing short-lived access JWTs may persist up to their configured lifetime. This action is not a full emergency MFA reset.

## New, guarded actions

The new endpoints are available only when
`PLATFORM_OWNER_EMERGENCY_RECOVERY_ENABLED=true`. The default is `false`.
The database migrations and image builds do not activate this flag.

- `GET/POST /api/platform/tenants/<tenant>/owner/emergency/`:
  list security cases / initiate a new MFA-only or full case.
- `POST /api/platform/tenants/<tenant>/owner/emergency/<case>/approve/`:
  a second platform operator reviews evidence and independently approves.
- `POST /api/platform/tenants/<tenant>/owner/emergency/<case>/dispatch/`:
  **the independent reviewer** sends the owner a 30-minute one-use consent
  link, no sooner than 24 hours after approval.
- `POST /api/platform/tenants/<tenant>/owner/emergency/<case>/cancel/`:
  authorized operator cancels an uncompleted case.
- `POST /api/auth/owner-emergency/confirm/`:
  only possession of the one-use capability from the preverified independent
  recovery email can execute recovery. The link does not provide a login.

## Threat model and authority boundaries

- The case target is always the database value `Tenant.owner`; client-selected
  `user_id` or `email` cannot redirect reset to another member.
- Initiator and reviewer must be **different** currently active Platform
  Administrators. An initiator cannot approve; reviewer cannot be the owner.
- Every privileged change requires a currently authenticated Platform Console
  access token carrying a recent platform emailed OTP verification claim
  (at most 15 minutes old) and the operator's current password.
- Staff review fields require an independent incident/evidence reference plus
  recorded verification of the owner's identity and company ownership.
  **The software records operator attestations; it cannot independently
  validate identification documents.** Operating procedure must check original
  evidence and authorization outside the system. Evidence should be referenced,
  not pasted as identity documents/secrets.
- The case is restricted to an active tenant owner whose account is NOT a
  Platform Administrator and whose independently verified recovery email is
  distinct from the primary MateMail user email.
- The verified address, verification timestamp, owner ID and security epoch
  are pinned at case creation and rechecked at all important transitions.
- The hold is server enforced; a browser clock cannot shorten it.
- Only the owner who receives the independent email can give final consent.
  No platform operator can see the confirmation capability, TOTP secret,
  backup codes, or the eventual password reset token.
- If the independent verified email has been lost or no second authorized
  platform operator exists, this automated procedure FAILS CLOSED. An
  independently designed/manual break-glass process is still needed; it is not
  silently implemented as a bypass here.

## Credential changes on successful owner consent

- **MFA reset**: delete the owner account's old Hub TOTP seed and all backup
  codes; increase the account-wide session security epoch; blacklist refresh
  tokens, revoke all API keys created by this account; require new authenticator
  enrollment even if admin policy is Optional/Off. The current password stays
  unchanged.
- **Full recovery**: everything above, plus set the existing password to
  unusable and issue a one-hour single-use `PasswordResetToken` **only to
  the independent verified recovery address**. Until the owner sets a new
  password and enrolls MFA, ordinary Hub access is impossible.
- The entire credential and audit mutation is atomic. For Full Recovery,
  delivery of the independent password reset message must succeed before
  committing any account mutation.
- Existing independent recovery email is not erased. This path has no access
  to PostBox mailbox messages or mail-engine passwords.
- The reset is user-account-wide: if the Owner belongs to several workspaces,
  all of their Hub sessions and API keys are affected.

## Safety and operational acceptance

1. Require independent written evidence and two separate staff accounts with
   verified platform email OTP; neither may be the owner being recovered.
2. Confirm the independent external recovery email is reachable before
   opening a security case. Never send a raw credential through staff chat.
3. Verify no mutation at request, second approval, hold or dispatch.
4. Verify owner cancellation and changed ownership/contact deny old links.
5. Verify reset causes immediate rejection of existing JWT access tokens,
   refresh cookies, backup codes and old TOTP.
6. Verify Full Recovery cannot use the old password; the owner chooses a new
   password through an independent email link and is forced to enroll TOTP.
7. Verify all actor, reviewer and completion audit events, and safe alerts.
8. Confirm that Mailbox/PostBox credentials and SMTP/IMAP remain untouched.
9. Keep the release flag OFF until the dedicated Phase 5 controlled release.
