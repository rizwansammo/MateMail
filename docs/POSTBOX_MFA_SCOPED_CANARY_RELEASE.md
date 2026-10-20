# PostBox MFA — Mailbox-scoped Canary Release Guard

**Scope:** Safe preparation for Stage 3; Stage 2 HTTP-to-Native End-to-End Acceptance remains **PENDING**.
**Production:** No activation, no mailbox changes, no credentials consumed.

## Why this guard exists

`POSTBOX_2FA_ENABLED=True` plus `SMTP_IMAP_SECURITY_ENABLED=True` previously routed **every** PostBox primary-password verification through the Native Engine. Before rollout that means non-canary users could be unexpectedly affected. Existing Native/Dovecot/Postfix isolated protocol acceptance alone does not prove it is safe to flip the global app switches on the real fleet.

## Explicit opt-in configuration

```env
# Only after separate operator approval and production-ready persistent Fernet key:
POSTBOX_2FA_ENABLED=False
SMTP_IMAP_SECURITY_ENABLED=False
# Explicit safety mode. True + empty/malformed IDs selects NO new mailboxes:
POSTBOX_2FA_CANARY_ENABLED=False
POSTBOX_2FA_CANARY_MAILBOX_IDS=
# Keep organization-wide REQUIRE locked:
ORGANIZATION_2FA_POLICY_ENABLED=False
ORGANIZATION_2FA_ENFORCEMENT_READY=False
```

The values above are **safe defaults**, not instructions to activate. For a future, separately approved canary, the operator first reviews a synthetic, provisioned **personal mailbox's immutable Django UUID**, key continuity, controlled rollback and production verification. That canary would explicitly set `POSTBOX_2FA_CANARY_ENABLED=True`, use only that UUID in `POSTBOX_2FA_CANARY_MAILBOX_IDS` and then consider globally enabling the two PostBox application gates. Never use a customer mailbox or guess a UUID. No GitHub workflow or database mutation is needed merely to review this file.

## Runtime isolation contract

| Mailbox state | Can begin new 2FA setup? | PostBox primary password verifier | MFA login |
| --- | --- | --- | --- |
| Canary selected, not enrolled | Yes | Trusted Native API | Ordinary web login until enrolled |
| Canary selected, enrolled | Already enrolled | Trusted Native API | OTP or one-use recovery code |
| Not selected, not enrolled | No | Existing IMAP verifier | Existing password-only login |
| Previously enrolled but removed from allowlist | Cannot begin new setup | **Still trusted Native API** | **Still must complete MFA** |
| Organization REQUIRE (if enabled independently) | Has independent lock | **Native API even if not on list** | MFA required; cannot bypass with IMAP |
| Canary mode TRUE, no valid IDs | Nobody new | Existing IMAP (unless already enrolled or independently Required) | Legacy for un-enrolled mailboxes |
| Canary mode FALSE | Global historical behavior | Native if both master flags ON | Unchanged existing controls |

These checks apply to PostBox web sign-in and to initial enrollment, new Connected App authorization's existing MFA test and client-credential management. Existing `mmc_` Connected App tokens remain governed by their independent authorization/revocation system, not SMTP/IMAP app passwords. Do not revoke them as a side effect of enrollment.

**Security invariant:** Removing an already-enrolled mailbox from the allowlist cannot downgrade or disable its MFA. Recovery/disable operations on such mailboxes remain accessible under their existing fresh-password + authenticator protections. Revoked credentials must still be denied by Native API/Dovecot/Postfix.

## Required acceptance gates before any Production Canary

1. All PostBox + Native tests and GH Actions CI green for the **exact** branch commit; build and publish artifacts normally, but do not deploy or flip flags.
2. Latest security patch deployed manually with all security flags OFF; production containers/migrations/legacy auth healthy.
3. Owner approves written, **mailbox-scoped** rollback, monitor plan, representative control mailbox tests and protected recovery key continuity.
4. User-operated real PostBox HTTP/browser -> enrollment -> Native credential -> live IMAP and SMTP for a synthetic account. The previous custom end-to-end script was blocked by execution safety controls; do not bypass it. Mark full E2E pending until an authorized process succeeds.
5. Verify a **non-canary mailbox** does not change login, SMTP/IMAP and mail delivery. Verify existing MateDesk/Helpdesk `mmc_` connection still reads/sends within granted permissions.
6. Gmail, Outlook and Thunderbird real client acceptance in Stage 3, and rollback/recovery rehearsal. Use only explicitly approved accounts and no personal secrets in chat/logs.
7. Organization REQUIRE must remain OFF; `can_activate_require()` remains locked independently.

## Rollback caution

Never remove an enrolled mailbox from the allowlist as a substitute for **disabling its 2FA**: the code intentionally keeps the enrolled mailbox's Native verifier and TOTP requirement. Do not turn BOTH master gates OFF casually for an already-enrolled mailbox, because the older feature-off semantics would stop checking its TOTP while Native may still reject its primary password on SMTP/IMAP. Recover or disable it using the authorized verified flow first, then decide on feature-flag rollback.

## TestLab scope and evidence

Earlier isolated, actual Native/Dovecot/Postfix protocol tests passed, including dedicated password acceptance, legacy rejection, revoked-credential rejection, active IMAP kick, IMAPS TLS, SMTP STARTTLS and independent mailbox access. A separate combined PostBox HTTP-to-Native test **did not pass and was not completed**; existing Django CI HTTP tests mock Native. All resources created under `/opt/TestLab/MateMail-2FA` for that attempt were removed (0 test containers, networks, volumes). Existing older Stage 7A artifacts were untouched. This branch adds synthetic CI compatibility tests but does not re-attempt the blocked custom integration test.
