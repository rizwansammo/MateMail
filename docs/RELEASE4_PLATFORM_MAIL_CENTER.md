# MateMail Systemwide Upgrade: Phase 6 (Release 4)

## Objective

Add a Platform Mail Center in the existing Platform Admin Console for the fixed system identities on \`system.matemail.pro\`. Reuse the production Native Engine, SMTP and IMAP. Do not share tenant PostBox sessions, system mailbox credentials or system message content with customer tenants.

Phase 5 (Release 3) has already migrated the sender and report receiver identities. Keep both legacy report inboxes polling until the later external reporting overlap is verified.

## Stages

| Stage | Scope | Gate |
| --- | --- | --- |
| 6A | Fixed Operations mailbox read-only folders and message headers, platform authorization, auditing, feature flag | Default OFF, no production activation |
| 6B | Operator-selected Operations message details, safe HTML/text rendering and attachment policy | Separate review of content security, UIDVALIDITY validation and download caps |
| 6C | Operations Compose, Reply, Sent, Drafts with explicit sender authorization, quotas and outbound safeguards | Separate review and live canary |
| 6D | Purpose-specific mailbox permissions: Security and Noreply; no unrestricted switching | Role grants and second-factor policy approved before enablement |
| 6E | Aggregate DMARC/TLS operational dashboards using existing parsed data, no raw report exposure to tenants | Tenant and platform data separation tests |
| 6F | Console UI, complete UX, end-to-end tests, controlled rollout and acceptance | Production health and rollback evidence |

## 6A: Developer acceptance

* \`GET /api/platform/mail-center/folders/\` reads only the fixed \`operations@system.matemail.pro\` mailbox and its selectable folder counts.
* \`GET /api/platform/mail-center/messages/?folder=INBOX&page=1&page_size=25\` reads only selected mailbox headers with IMAP readonly SELECT, bounded pages, no body or preview FETCH.
* Every successful read is recorded in the platform audit log without message subjects, sender addresses, bodies, or secrets.
* Platform-only authenticated sessions; tenant sessions, anonymous callers and API keys denied by existing \`IsPlatformAdmin\` permission and platform middleware.
* No mailbox address from HTTP request may select a different identity. Only literal fixed Operations mailbox is used by the backend.
* Missing credential, IMAP errors, or disabled flag fail closed with generic response; no internal exception text exposed.
* \`PLATFORM_MAIL_CENTER_ENABLED=False\` by default, including production Compose. Do not turn it ON during 6A.
* No migrations, DNS, mailbox activation, SMTP, system sender, Native Engine or customer mail routing changes.

## Next

After 6A tests and CI pass, plan and implement 6B in its own small branch. The whole Phase 6 is **not complete** until the remaining stages are reviewed, validated, deployed and accepted.
