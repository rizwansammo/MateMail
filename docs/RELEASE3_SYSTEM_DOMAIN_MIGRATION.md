# MateMail Release 3: System Domain Migration

Status: **GATED. Do not cut over.** 14 October 2026.

## Scope

Target: `system.matemail.pro`, reserved to PLATFORM and unavailable to organizations.

Legacy: `mail.matemail.pro` must continue to accept inbound mail, sign outgoing mail, receive DMARC and TLS reports, and authenticate the existing `noreply` service account during the entire transition. Do not rename, rekey, delete, disable, or migrate it as a side effect.

The five planned target identities are `noreply`, `security`, `operations`, `dmarc`, and `tlsrpt`. `operations` is intended for the future Platform Mail Center. An inactive, passwordless placeholder is **not** a working mailbox.

## Confirmed starting position

- Django ownership reservation exists for both system and legacy domains.
- Native Engine schema 009 is deployed.
- Legacy Native Engine domain has three active mailboxes.
- Target Native Engine domain is absent.
- Native `engine_platform_operator` is NOLOGIN.
- `NATIVE_PLATFORM_OPERATIONS_ENABLED=False`.
- `SYSTEM_MAIL_STAGING_ENABLED=False`.
- Platform staging and runtime secrets are not provisioned.
- Transactional SMTP connects to `mx.matemail.pro:587` and authenticates using the existing `mail.matemail.pro` identity. The actual mailer owns this design (DEC-013); a stale settings comment claiming an external provider is incorrect.
- DNS for target A, MX, SPF, DKIM and DMARC has not been published.

## Required records for the target

Do not replace existing `mail.matemail.pro` DNS records.

| Type | Owner | Value | Gate |
| --- | --- | --- | --- |
| MX | `system.matemail.pro` | `10 mx.matemail.pro.` | Prior to inbound tests |
| TXT | `system.matemail.pro` | `v=spf1 include:_spf.matemail.pro -all` | Verify outgoing path and SPF alignment |
| TXT | `_dmarc.system.matemail.pro` | `v=DMARC1; p=none; rua=mailto:dmarc@mail.matemail.pro` | Monitoring policy during validation, retain legacy report receiver until new reports verified |
| TXT | `mm1._domainkey.system.matemail.pro` | **Not yet available**. Use the exact public key issued by the future dedicated Native Engine DKIM operation. Never copy the old private key. | Signed real test and key match |

Do not invent the DKIM public key or make up a successful DNS response. A matching hostname does not prove key ownership.

An A record for `system.matemail.pro` is not inherently required for SMTP receive when its MX points to `mx.matemail.pro`. Do not point the root of `system.matemail.pro` to a web application without a separate requirement.

## Release gates

1. **Backup and ownership:** Fresh application and Native Engine database backups, DKIM keys and Maildir included in a verified restore plan. Reserved ownership remains unchanged.
2. **Dedicated stage credentials:** Native platform operator credential must be strong, distinct from every API, Postfix, Dovecot and database secret, never exposed in logs. Enable staging only after isolated NOLOGIN role activation and DB policy tests; the existing role cannot log in today.
3. **Inactive stage:** Invoke only the fixed, audited, allowlisted command. Validate one inactive domain and five inactive, passwordless, login-disabled role placeholders; ensure old mailboxes and DKIM untouched.
4. **Provision and activate independently:** Provide a separately reviewed Engine endpoint with fixed role allowlist for credential setup, DKIM issuance and activation. Generic tenant APIs are forbidden on platform-owned domains. No endpoint exists yet. Do not activate via raw SQL or disable ownership triggers.
5. **Publish and verify DNS:** Stage target MX, SPF and monitoring DMARC safely. DKIM TXT must match Native Engine key. Observe TTL and public resolvers.
6. **Real delivery:** Send new-domain outbound SMTP through authenticated platform service account, check actual Gmail authentication results, and inbound IMAP reception into the target. Verify DMARC/TLS report routing separately. Only then switch one sender purpose at a time.
7. **Rollback rehearsal:** Explicitly preserve previous Compose, image SHA, sender and report receiver env values. If any new send fails, restore legacy sender settings and app images; do not delete target data or old DNS. A database rollback must not erase mail delivered during the test.
8. **Final acceptance:** Check SMTP, IMAP, DKIM, SPF, DMARC, report ingestion, password reset, platform admin access, native DB permissions and tenant isolation. Compare mail logs against preflight baseline.

## Implemented in this feature branch

The platform-only `GET /api/platform/system-mail/dns-readiness/` endpoint exposes **observational** DNS state using bounded public DNS lookups. Errors and duplicate SPF fail closed. DKIM publication remains `published_key_unmatched`; it never asserts private-key match or mail delivery. Every response has `cutover_ready:false`.

No engine staging, DNS mutation, mailbox activation, sender change, or cutover is caused by this feature. Production legacy configuration remains authoritative.

## Current production blocker

A working release requires new target DNS records and a separately tested Native Engine authorization/credential/DKIM/activation mechanism that does not yet exist. Until those gates and a real mail flow test pass, describing Phase 5 as migrated would be unsafe and inaccurate.
