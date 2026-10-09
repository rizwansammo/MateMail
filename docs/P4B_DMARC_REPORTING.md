# P4-B — DMARC Aggregate Reporting (Safe Rollout)

**State:** reporting backend candidate. Existing DNS, DKIM secrets, inbound mail flow and customer mailboxes must not be touched by deploying this code.

## Architecture

- Recipient mailbox: `dmarc@mail.matemail.pro` (already exists in the Native Engine; do not create a duplicate).
- Transport: existing private PostBox IMAP master-gateway access over TLS, authenticated to the **fixed** DMARC mailbox only, and `SELECT INBOX (READ-ONLY)`. Never flag, move, delete, or alter customer email.
- Ingestion: Celery task `dmarc_reports.poll_mailbox` every 30 minutes, **NO-OP unless DMARC_REPORT_INGEST_ENABLED=true**. Redis lock stops competing workers; persisted IMAP UIDVALIDITY+UID cursor handles retries/folder resets. Up to 60 messages/run; each message at most 6 MiB and eight bounded XML documents.
- Formats: attached XML, GZIP XML, ZIP XML; no extracted filenames are written to disk. ZIP traversal, unsafe symlinks, extreme decompression ratios, oversized archives, massive record counts, invalid dates, duplicate records and malicious DTD/entity XML are rejected.
- Persistence: only per-domain aggregate metadata, counts and sender source IP rows, scoped to existing **ownership-VERIFIED** `Domain` records. `mail.matemail.pro` is a platform-only report (NULL tenant/domain); unowned/unverified domains are NOT silently attributed to the wrong tenant.
- Duplicate detection: sha256 of unpacked XML content, unique in DB.
- Privacy: no email text, subject, attachment filename, report XML, recipient address or private key is kept in the reporting database. Sender IPs and aggregates are kept up to 90 days by default; `dmarc_reports.prune` runs daily.
- Viewing: tenant **Owner/Admin** read-only `GET /api/domains/{uuid}/dmarc-reports/?days=30`; platform admin `GET /api/platform/dmarc-reports/?days=30` sees only the platform's own sender domain. Neither API is public or allowed to search another tenant.
- Reports are untrusted third-party **telemetry**; never automatically update tenant DNS or toggle DMARC enforcement from report data.

## Deployment safety — intentionally staged

1. Merge only after Django migrations `--check`, Django tests, XML fuzz/zip regression, PostBox/tenant integration and CI are green.
2. Take validated Azure and database backups. Apply the additive migration `dmarc_reports/0001_initial` via the existing manual MateMail deployment workflow; old tables or mailstore are not modified.
3. Initially keep `DMARC_REPORT_INGEST_ENABLED=false` **AND** `DMARC_AGGREGATE_REPORTING_ENABLED=false`. UI/read API shows zero reports, as expected. Confirm all containers, migrations and PostBox authentication remain healthy.
4. Activate ingestion **separately**, after operator approves the limited read-only DMARC mailbox process. Require working PostBox master access and at-rest DB permissions; configure `DMARC_REPORT_INGEST_ENABLED=true` in root-only app environment, then redeploy the application containers under a safe rollback plan. Confirm cursor/last-success without exposing mailbox credentials. It does not itself publish any RUA DNS record.
5. Before publishing a DMARC `rua=` on a customer domain such as `netamate.com`, publish an **external reporting authorization** TXT record in the *report receiver's* `mail.matemail.pro` DNS zone. For example, when reports for `netamate.com` are to be sent to `dmarc@mail.matemail.pro`, the receiver authorization record is `netamate.com._report._dmarc.mail.matemail.pro TXT "v=DMARC1"`. Validate with authoritative DNS and multiple public resolvers; the configured report inbox is not sufficient by itself.
6. Update each existing DMARC TXT at `_dmarc.<sending-domain>` **in place** (NEVER publish a second DMARC policy) from `v=DMARC1; p=none` to `v=DMARC1; p=none; rua=mailto:dmarc@mail.matemail.pro` only after DNS authorization and receiver verification. For `mail.matemail.pro` itself, external authorization is unnecessary, but DNS record changes still need approval.
7. Receive real aggregate reports over several days and verify independent mailbox headers/inbox evidence from owned test accounts (Gmail/M365/Zoho). Do NOT promote a domain to `p=quarantine` or `p=reject` based on configuration or one report: check actual sending paths, third-party ESPs, forwarding and feedback; changes per tenant need approval.

## Explicit configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DMARC_REPORT_ADDRESS` | `dmarc@mail.matemail.pro` | Fixed read-only report mailbox |
| `DMARC_REPORT_INGEST_ENABLED` | `false` | Enable IMAP polling separately |
| `DMARC_AGGREGATE_REPORTING_ENABLED` | `false` | Existing onboarding DNS hint only; **not** DNS editing |
| `DMARC_REPORT_PLATFORM_DOMAIN` | `mail.matemail.pro` | Platform-only sender reports |
| `DMARC_REPORT_RETENTION_DAYS` | `90` | Remove old aggregate data and IP rows (7–365 days) |

No public ingress endpoint is exposed for XML uploads; only MIME fetched from the fixed mailbox is considered. `p=none` remains the enforced policy until a separate authorised per-domain decision.

## Caveats

- DMARC aggregate XML does **not prove** a message arrived in Inbox or even that the reporting organization is authentic. SMTP delivery, Gmail Postmaster Tools, Microsoft SNDS, etc. remain separate signals.
- ZIP files are untrusted; maximum payload and counts apply to both compressed and uncompressed representations. Reports that exceed safe limits are intentionally rejected. Operators may inspect original retained email outside this service.
- Source-IP telemetry is sensitive; avoid leaking or sending raw data to analytics platforms. Tenant-admin role restrictions and default 90-day retention apply.
- DMARC `rua` delivers aggregate reports, not per-message forensic reports; avoid `ruf` unless a privacy and legal review explicitly approves it.
