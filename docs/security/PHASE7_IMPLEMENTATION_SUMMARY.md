# MateMail Phase 7A–7C Security Implementation Summary
**Documentation status:** Historical implementation synthesis, reviewed for Phase 8 on 2026-10-23.

## Phase 7A: foundation
Hub and PostBox have separate security identities, with TOTP foundations, SMTP/IMAP dedicated credential architecture, Native verifier, tenant isolation, fail-closed authentication and compatibility boundaries.

## Phase 7B: flows and user experience
Hub and PostBox enrollment, QR-code setup, OTP challenge, recovery codes, backup-code regeneration and invalidation, app-password management/revocation and organization security UI were developed. Owner's previous manual Hub Google Authenticator login test succeeded; do not silently interpret it as a new Phase 8 test.

## Phase 7C: Native enforcement and release
Organization `off`, `encourage` and `require` policy; domain/tenant binding; Native schema 016; enforced dedicated client credentials; primary password rejection when required; revoked credential denial; IMAP session kick and durable recovery; unrelated tenant isolation; and new-mailbox policy inheritance.

Owner/Admin controls each organization's policy. Platform administration does not automatically enforce MFA.

PR #152 merged to `1638299f6046e8493d74e64077ef7599b45ba6d7`. Phase 8 matched the running backend/frontend images and Native API digest to the intended release. Phase 7C isolated acceptance and unit regressions are documented in the existing reports and server-only evidence logs.

## Outstanding distinction
An end-to-end production tenant-owner selection of `require`, together with representative Gmail, Outlook or Thunderbird authenticated flows, was not reconfirmed in Phase 8. This is not implied by successful isolated integration tests or green CI.

## No new implementation
This Phase 8 read-only review created no application feature changes or production release.
