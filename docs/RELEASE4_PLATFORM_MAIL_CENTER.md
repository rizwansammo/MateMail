# MateMail Systemwide Upgrade: Phase 6 (Release 4)

## Objective

Add a Platform Mail Center in the existing Platform Admin Console for the fixed system identities on \`system.matemail.pro\`. Reuse the production Native Engine, SMTP and IMAP. Do not share tenant PostBox sessions, system mailbox credentials or system message content with customer tenants.

Phase 5 (Release 3) has already migrated the sender and report receiver identities. Keep both legacy report inboxes polling until the later external reporting overlap is verified.

## Stages

| Stage | Scope | Gate |
| --- | --- | --- |
| 6A | Fixed Operations mailbox read-only folders and message headers, platform authorization, auditing, feature flag | Default OFF, no production activation |
| 6B | Operator-selected Operations message details, safe HTML/text rendering and bounded attachment downloads | Strict UIDVALIDITY, MIME bounds, no inline rendering, security tests; code gated OFF |
| 6C | Operations plain text Compose, Reply, Sent filing and IMAP Draft save, replace and delete | Two independent feature gates OFF, CI + live canary required |
| 6D | Explicit expiring Security/Noreply read grants, OTP proof, auditable approval/revocation and bounded read-only APIs | Default OFF, no implicit switching or interactive sending |
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


## 6B: Message reader and attachments

The backend accepts only the **literal** Operations platform mailbox. Privileged API calls
use \`IsPlatformAdmin\` with the same authenticated platform API safeguards as 6A.
The existing \`PLATFORM_MAIL_CENTER_ENABLED\` remains disabled by default.

* \`GET /api/platform/mail-center/messages/{uid}/?folder=INBOX&uid_validity={value}\`
  requires a fresh positive UIDVALIDITY, an existing selectable IMAP folder, and a
  bounded 1..4294967295 UID. A stale UIDVALIDITY fails with HTTP 409, **before
  any raw message is fetched**.
* Server checks RFC822.SIZE and actual received MIME against 4 MiB; rejects more than
  24 attachments or text/HTML over 250,000 characters.
* HTML is passed to the existing battle-tested PostBox \`mime.parse_message\`
  sanitizer with \`load_remote_images=False\`, even if requested otherwise.
  The caller receives text and sanitized HTML. **Render text as text and sanitized
  HTML in an isolated mail viewer, never with privileged inline scripting**.
* \`GET /api/platform/mail-center/messages/{uid}/attachments/{part_id}/?folder=...&uid_validity=...\`
  requires the same reference and verifies the requested part exists among parsed
  attachments. Only up to 2 MiB; \`Content-Type: application/octet-stream\`,
  \`Content-Disposition: attachment\`, \`nosniff\`, \`sandbox\`, and no-cache
  prohibit same-origin inline execution and browser caching.
* Read-only IMAP SELECT and FETCH; no \`\\Seen\`, mailbox mutations, message downloads
  from other identities, SMTP actions, new credentials, or Native Engine changes.
* Audit stores actor, fixed system mailbox, generic folder role, and download
  byte count; **never** message body, filename, subject, sender or attachment contents.
* No Production flag change, migration, mail routing or DNS change in 6B.

6C will add controlled Operations outbound sending and draft management behind
a separately reviewed gate; no other system mailbox can be selected implicitly.

## Phase 6C: Secure Operations sending and drafts

New routes:

- POST /api/platform/mail-center/send/
- POST /api/platform/mail-center/drafts/
- DELETE /api/platform/mail-center/drafts/{uid}/?uid_validity={positive integer}

Both PLATFORM_MAIL_CENTER_ENABLED and PLATFORM_MAIL_CENTER_SEND_ENABLED must be true. Both default OFF, including production Compose. The read-only Phase 6A and 6B endpoints do not need the new outbound gate.

Sender is always operations@system.matemail.pro, authenticated using the existing PostBox master-identity SMTP STARTTLS path and the Native Engine sender-login policy. No customer mailbox, role or credentials are selected from request input. Sending reuses the PostBox MIME builder and SMTP submission service.

Only plain text messages are accepted in the initial phase. Client-provided sender identities, HTML, attachment uploads, scheduled sends and arbitrary threading headers are rejected. At most 10 distinct envelope recipients per message; subject max 300 chars; text max 50000 chars.

Every Send request requires a 16 to 80 character opaque idempotency_key. Atomic Redis claim prevents duplicate submission by the same administrator for 24 hours, including after a transient or ambiguous failure. Rate caps: 10 attempted sends per platform admin and 30 per Operations mailbox per hour. The Native Engine still applies its own caps.

SMTP submission precedes IMAP Sent append. Failure to append a copy must not falsely report the mail failed or encourage sending again. Bcc recipients exist only in SMTP envelope and never in a sent message header.

Reply recipients, In-Reply-To and References come only from an authorized, UIDVALIDITY-verified original message in the fixed Operations mailbox. Reply-All cannot name any arbitrary platform identity. Source selection uses read-only IMAP.

Draft Save writes a real MIME message to the Operations IMAP Drafts folder with Draft and Seen flags and retains Bcc only in the local draft MIME copy. Replacement checks original UIDVALIDITY and draft flag, then APPENDs the new message before deleting the old one; an interrupted replacement must not lose the original. Draft delete requires the matching current UIDVALIDITY and explicit DELETE.

Every outbound or draft action writes a platform audit attempt before the side effect; only non-sensitive counts and result flags may appear in audit metadata. No recipient address, subject, text, filename or content is written to the audit log.

No Phase 6C production activation, deployment, Engine updates, DNS changes, migrations or customer mailbox modifications are included. Enable flags only after later controlled canary and Phase 6F acceptance.


## Phase 6D: Security and Noreply mailbox access

Security and Noreply mailboxes are **not** interchangeable with the Operations
mailbox. A fixed server allowlist maps the role 'security' exclusively to
security@system.matemail.pro and 'noreply' exclusively to
noreply@system.matemail.pro. No arbitrary user supplied mailbox ID or address
can select a tenant mailbox, reporting mailbox or another platform identity.

A sensitive mailbox read is authorized only when all of these hold:

1. The caller is an active Platform Admin, authenticated without an API key.
2. The current access JWT contains a valid platform_otp_verified_at timestamp
   issued by the Platform Console after successful email OTP challenge
   verification. It is valid for a maximum of 15 minutes. Normal tenant tokens
   and unverified administrator tokens do not carry this claim.
3. PLATFORM_MAIL_CENTER_ENABLED and the independent
   PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED are both True. The latter is
   disabled by default, including in production Compose.
4. A grant exists for this administrator and exact role that has not expired
   or been revoked. The new grant table starts empty; even a superuser does
   not receive implicit access.

Approval: an OTP verified platform superuser can grant **only another** active
platform administrator temporary read permission for one role (1..168 hours).
Self-grants, tenant users, unlisted roles and indefinite permissions fail
closed. Approval and revocation must be atomically audited. No recipient,
subject, body or credential is stored in platform audit metadata.

### Sensitive mailbox endpoints

- GET /api/platform/mail-center/system-access/ returns only the requesting
  administrator's unexpired role grants.
- POST /api/platform/mail-center/system-access/grants/ approves one temporary
  role with grantee_id, role and duration_hours (platform superuser only).
- DELETE /api/platform/mail-center/system-access/grants/{uuid}/ revokes it.
- GET /api/platform/mail-center/purpose/{role}/folders/
- GET /api/platform/mail-center/purpose/{role}/messages/
- GET /api/platform/mail-center/purpose/{role}/messages/{uid}/
- GET /api/platform/mail-center/purpose/{role}/messages/{uid}/attachments/{part_id}/

Every mail read uses read-only IMAP, strict UIDVALIDITY on full reads,
bounded bodies and attachments, sanitized HTML and blocked remote images.
Attachments are download only, application/octet-stream, sandbox and
nosniff. All message actions other than GET are rejected. **No interactive
sending as security or noreply** is added; the Phase 6C outgoing route still
uses only operations@system.matemail.pro.

The new database migration creates an empty grant table. The new feature
flag stays disabled for development and production until controlled approval
during 6F; no customer mailbox, DNS, SMTP or Native Engine setting is changed.
