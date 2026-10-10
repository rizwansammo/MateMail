# MateMail Astra Hub — Phase 5: optional mailbox provisioning

**State:** feature branch only. DO NOT merge or deploy without explicit approval.

## Behavior
- Admin/Owner may choose **Create a mailbox for this user** when inviting.
- The default invitation remains Hub-access-only. No mailbox is created.
- When selected, the new personal mailbox uses the invitee's exact email.
- Only an approved mail-enabled tenant on a verified domain is eligible.
- Plan capacity, default quota, and global address reservations are enforced.
- The invite does **not** store a mailbox password. The invited user chooses
  a separate password on invitation acceptance or invite-bound signup.
- An invite can be revoked/expire without provisioning any mailbox.
- The invited user must accept with the same email; no workspace switching.
- The owner/admin POST response returns a **one-time** invite link so they
  can share it if the new mailbox does not yet receive email. GET never shows
  the raw token/link.

## Transaction and partial failure
- On acceptance, tenant lock serializes resource accounting.
- Domain ownership, mail-enable gate and address claim are checked again.
- Mailbox DB address reservation, member creation and invite consumption
  commit atomically. Any validation error leaves the invite pending.
- Mail Engine provisioning is an external network operation; it happens
  *after* database commit. It cannot be rolled back by a Django transaction.
- If engine provisioning fails, the local mailbox stays pending/repairable,
  with a safe customer message. Existing Mailboxes reprovision API handles
  repair. Do not silently claim an external rollback.
- A live tenant/staging acceptance test is necessary before release.

## Quality gates
- Backend: legacy off; opt-in; verified-domain restriction; plan cap;
  existing address; signup/accept; wrong password; retry; revocation.
- Browser: opt-in/opt-out UI, member roles, required password, no secret
  persistence, one-time link, no unintended API mutations.
- CI: run only the existing workflow set on a single consolidated commit;
  do not trigger needless per-fix parallel runs.
- Production: explicit user approval, backup/restore readiness and staging.

## Phase 6 security gate — verified *provisioning administrator*
The ordinary Mailboxes API checks `IsEmailVerified` on the authenticated
workspace administrator who **creates** the mailbox, not on its future
PostBox/mailbox account holder. Optional invited mailbox creation now enforces
the equivalent rule twice: at invite issuance and again before DB reservation
on acceptance/signup. Plain Hub-only invitations remain allowed, even for
unverified Hub admins (no Mail Engine action).

Do **not** insist on the recipient verifying an email address before creating
the mailbox at that same address: if it does not exist yet, a verification
email cannot reach it. A new invited Hub user remains `email_verified=False`
until the normal verification email is sent *after* the requested personal
mailbox is provisioned. A valid invite link is a privileged bearer secret;
admins must share it privately and only with its intended recipient.

Security properties verified by tests: no mailbox request from an unverified
admin; an old invite from an unverified issuer cannot be redeemed for mailbox
creation; no user, membership or mailbox row remains after a denied signup;
no Mail Engine call occurs on denied paths.
