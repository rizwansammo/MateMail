# Release 3: dual DMARC and TLS report receivers

## Goal
Keep the existing working `mail.matemail.pro` inboxes ingesting reports while
validating new `system.matemail.pro` report identities. No DNS, sender or
report publication is changed by this feature.

## Guardrails
- Both `DMARC_REPORT_DUAL_RECEIVER_ENABLED` and
  `TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED` default to `False`.
- DMARC and TLS ingestion's existing feature gates remain authoritative.
- Each poll runs the **legacy** mailbox first, then the **system** mailbox.
  Both use distinct persisted UID/UIDVALIDITY cursors; report content hashes
  already deduplicate reports that reach both addresses.
- One existing cache lock spans both receivers per report type. Failures
  preserve the failed mailbox's cursor for retry.
- Only fixed `dmarc@` and `tlsrpt@` addresses on the two platform domains
  may be polled. Never accept arbitrary or tenant addresses.
- Read-only IMAP and TLS-RPT DKIM verification are unchanged.
- `DMARC_REPORT_ADDRESS` and `TLS_RPT_REPORT_ADDRESS` remain on
  `mail.matemail.pro` while this dual-poll feature is enabled.
  These settings affect DNS publication in other components. Do NOT switch
  their values as part of enabling dual polling.

## Controlled rollout
1. Verify a fresh database and mail backup, five healthy system identities,
   old report tasks running and the old report cursors.
2. Deploy the tested backend image and Compose env passthrough with
   both new flags OFF. Verify both old ingestion tasks still work.
3. Enable both dual polling flags and update backend, worker and beat.
   Verify read-only access to both receivers, two distinct cursors per report
   type, no cursor reset on the legacy receiver, no authentication exceptions
   and no loss of previous report processing.
4. Inject or receive **valid, authenticated** sample reports to the new
   receivers. Exercise content-hash deduplication across old/new inboxes and
   verify the same tenant isolation. An ordinary test email only proves
   delivery, not parser or authentication acceptance.
5. Only after full evidence, separately prepare DNS publication changes with
   legacy overlap. The system domain's existing DMARC RUA and tenant TLS-RPT
   destinations need review; do not assume changing a setting updates all DNS
   zones. External report authorization may need a separate record.
6. For rollback, set dual receiver flags OFF again and restart backend,
   worker and beat. Do not delete new-domain mail, the old receiver,
   DNS records or per-address cursors.

## Feature flags
```env
DMARC_REPORT_DUAL_RECEIVER_ENABLED=False
TLS_RPT_REPORT_DUAL_RECEIVER_ENABLED=False
```

Current Release 3 transactional sender migration is independent of this
receiver migration. Preserve legacy report recipient acceptance until its
published DNS and historical report overlap is independently verified.
