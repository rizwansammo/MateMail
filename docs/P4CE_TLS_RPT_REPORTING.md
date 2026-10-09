# P4-C.E — Multi-Tenant TLS Reporting and Monitoring

Development-only implementation. No production, DNS, Nginx, SSL, email-routing or mailbox changes in this phase.

## Boundaries
- Separate fixed recipient: tlsrpt@mail.matemail.pro. NOT the DMARC mailbox or XML parser.
- Existing PostBox IMAP master gateway opens only the fixed receiver with INBOX READ-ONLY. It never marks mail as read, moves, deletes or sends anything.
- TLS_RPT_INGEST_ENABLED defaults OFF. A Celery poll every 30 minutes becomes a NO-OP while disabled. The cache lock and persisted UIDVALIDITY+UID cursor prevent overlapping reads and skip/retry errors safely.
- Bounded RFC 8460 JSON and GZIP MIME parser. Max message 6 MiB, compressed attachment 4 MiB, expanded JSON 2 MiB, 8 attachments, 32 policies per attachment, 256 failure entries per policy; strict JSON types, duplicate key rejection and dates.
- Persist only domain-bound aggregated counts, reporter, policy type, timestamp, and failure result-type buckets. Do NOT store raw email, JSON, IPs, per-session details, free text, contact details or MX names.
- Only current ownership-verified and explicitly opted-in domain configurations can receive tenant reports. Report windows starting before current ownership or opt-in are discarded. All reports remain untrusted external telemetry.
- Tenant Owner/Admin: GET /api/domains/{uuid}/tls-reports/?days=30. Cross-tenant access returns 404. Hub reports page is linked from Advanced security.
- Platform-only operational GET /api/platform/tls-reporting/health/ returns disabled, stale or operational status, last checked timestamps and aggregate intake/rejection counts without tenant details.
- Daily prune after 90 days by default (bounded 7–365 days); original email is preserved.

## Fail-closed gates
TLS_RPT_INGEST_ENABLED=False (read mailbox).
TLS_RPT_RECEIVER_VERIFIED=False (operator confirmation of real receiver, privacy and ingestion).
TLS_RPT_DNS_PUBLICATION_ENABLED=False (allow DNS TXT only when BOTH previous gates true).
All are separate. The TLS-RPT destination must match the dedicated mailbox. Standard onboarding and existing transport-security gates remain unaffected. MTA-STS continues in testing mode; never silently enable enforce.

## P4-C.F rollout steps — NOT executed
1. Validate backups and restore plan; deploy only an approved CI-green SHA using the existing manual workflow, confirming migration and Compose wiring with feature flags OFF.
2. Independently verify the TLS-RPT mailbox exists in Native Engine and read-only IMAP works with UIDVALIDITY and scoped credentials.
3. Enable ingestion alone, then send real controlled JSON and GZIP reports from external mail. Verify cursor, dedupe, parsing, no IP/payload leakage and tenant boundaries. No DNS publication yet.
4. Check platform health status and stale checks. Confirm receiver-side authorization requirements for external reporting addresses and authoritative DNS. Only then approve dedicated receiver and publication gates, staged per domain.
5. Confirm no effect on ordinary mail. Migrate fixed legacy MTA-STS hosts safely only in P4-C.F, maintain rollback and validate real reporter data over time.

## Validation
Full Django tests (including tests.test_tls_reporting), frontend lint/build, migration check, docker compose flag pass-through and runtime smoke test.

Next: P4-C.F production rollout and P4-D final evidence-based security audit.
