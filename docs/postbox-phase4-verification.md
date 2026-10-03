# PostBox conversation feature — Phase 4 verification and release gate

This document separates **automated evidence** from **manual live-mail acceptance**. Do not describe mocked provider fixtures or an unauthenticated HTTP response as Gmail/Outlook delivery proof.

## Scope and isolation

Changes remain in `feat/postbox-clean-reply-phase1` / draft PR #30. Never run destructive, bulk, performance or unsolicited external-mail tests against production mailboxes. Set up an owner-approved isolated staging environment running the exact pull-request commit, with test-only MateMail and NetaMate Email identities and dedicated Gmail and Microsoft Outlook test mailboxes. Do not store passwords, tokens, raw customer mail, cookies or complete mail headers in this issue, PR or logs.

## Automated phase-4 gate

- The full Django test suite and additional regression suite must both succeed at the **latest commit**.
- MateMail PostBox and NetaMate Email lint/build, plus production Compose validation, must pass at the same commit.
- Tests include own-Sent-last-message Reply and Reply All recipient selection; representative Gmail/Outlook-style MIME with multi-message References, HTML and text/plain; clean Reply body; opt-in quoted original; signature/draft/scheduled-send behavior; rejected stale UIDVALIDITY for reply, original MIME and attachments; cross-folder deduplication, folder exclusions, pagination and 5,000-header graph capacity.
- A green build is **not** proof that real browser, SMTP delivery, external IMAP mailbox sync or accessible navigation has been exercised.

## Staging browser checklist (sign-off required)

Test Chrome desktop/mobile-width and Firefox (or an equivalent second engine), in both Light and Dark. Test keyboard Tab, Enter, Space and focus indicators; use a screen reader for message-card expanded state, dialogs/regions, toggles and the inline editor.

1. Sign in to the staging mailbox. Inbox should default to Conversations; Messages remains available and accurately lists raw per-folder messages. Searches, Unread, Starred, sort changes, Spam, Trash, Drafts and Scheduled retain their existing views and actions. A deliberately over-cap staging mailbox should show the stated fallback instead of a partial conversation.
2. Open a three-message thread with Inbox/Sent/Inbox messages. Check single conversation row, correct count/participants/unread state, chronological cards, latest opened and older collapsed. Expand an older card: only then fetch its sanitized body. Collapse and expand using keyboard and touch without scrolling the whole app.
3. Reply to the newest **Sent** message, then Reply All. Check the original external recipient appears in To; own identity never appears in To/Cc, additional participants are preserved once, and the composer body starts empty. Quote is opt-in and previewed separately.
4. Start writing an inline reply, attach a test file, select a signature and wait for a confirmed Draft save. Try opening another thread, navigating via sidebar and changing the single/conversation view: none may discard an active inline composer silently. Check hard refresh warns on unsaved edits. Close only after verified save; reopen Draft and confirm text, recipients, attachment references and optional quote.
5. Test sending, scheduling, cancel/reopen, Forward, remote-image privacy (one-time and per-sender trust), image CID previews, attachment download and PDF/image preview. HTML/SVG/XML should not be granted unsafe same-origin inline execution.
6. Verify per-message Star, Unstar, Read, Unread, Archive and Trash on copies. Explicitly recreate a test folder or alter its UIDVALIDITY; stale message/detail, attachment, preview and reply operations must fail without fetching another message.

## Live transport matrix (requires owner-approved test accounts)

Use unique benign subjects (for example `MATEMAIL-THREAD-QA-20261004-001`) and inert sample attachments. Never use a customer mailbox or external address without permission.

| Sequence | Action | Required evidence |
| --- | --- | --- |
| A | Staging MateMail to dedicated Gmail; Gmail replies; staging MateMail replies again | Exactly one conversation; Received/Sent copies deduplicated; correct RFC `Message-ID`, `In-Reply-To` and `References`; no default raw `>` history |
| B | Staging MateMail to dedicated Outlook; Outlook Reply All with another permitted test recipient | Reply-To precedence, external recipient deduplication, HTML/text alternatives and a correctly ordered thread |
| C | Staging NetaMate Email to Gmail and Outlook and replies back | Same shared backend/UI behavior with independent branding and mailbox isolation |
| D | Separate test-only third-party IMAP/SMTP client moves and flags a conversation message | Refresh reconciles the new folder, preserves correct copies/unread state and rejects stale UID references |
| E | Mobile-width browser opens long HTML and plain-text quote-heavy chains | Readable thin-border cards, quoted history collapsed when recognized, no background page scrolling |

Collect minimal redacted evidence: provider-visible Received/Sent delivery states, RFC identifiers, expected recipient lists, relevant browser outcomes and timestamps. Do not publish live full message sources or auth material.

## Readiness decision

**Automated CI green alone is insufficient for production approval.** Record the staging build SHA, verified test accounts (role/provider only), redacted proof and unresolved findings. If provider accounts/staging are not available, mark those rows BLOCKED rather than PASSED. Only after owner review may the owner merge to `main` and carry out the MateServer deployment, followed by a short, non-destructive production smoke test.
