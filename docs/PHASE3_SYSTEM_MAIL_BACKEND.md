# Phase 3: Dynamic System Configuration Backend

## Scope

Platform Admin API: `GET|POST /api/platform/system-mail/configuration/`

This endpoint maintains **immutable versioned proposals only**. Saving a
proposal cannot change DNS, SMTP, IMAP, authentication, DKIM, public transport
security, domain ownership, a running task, or the Native Engine. No password,
API credential, private key or mailbox message content is accepted or returned.

The intended platform domain is strictly `system.matemail.pro`. Addresses are
role-specific: `noreply`, `security`, `operations`, `dmarc`, `tlsrpt`.
Legacy `mail.matemail.pro` remains the actual system sender and reporting
receiver until controlled cutover. The response discloses effective values
from environment and has explicit `draft_applied:false` and
`activation_supported:false` indicators to prevent operator confusion.

A request must provide every field plus the current `expected_revision`.
Concurrent updates return HTTP 409 instead of silently overwriting.
Every successful write appends a platform audit entry in the same transaction,
without storing secrets.

## Separate activation gate

Phase 3 has deliberately **no activation endpoint**. System Mail platform
ownership must already be seeded by Phase 2. Authorized Native Engine system
mutation, mailbox credential provisioning, DNS validation, secret storage and
live sender/receiver switch are further rollout dependencies. No UI should
claim a proposal is active.

## Release

Phase 2 + Phase 3 are reviewed and deployed together only after independent
CI, current backups, reverse migrations and explicit approval. All new
behaviour must be regression tested against the existing tenant APIs and
production-shaped Postfix/Dovecot.

## Additional Integrity Guarantees

The revision table is append-only at the PostgreSQL layer. Any UPDATE or
DELETE is rejected by a database trigger, not merely by the API view.
Reverting the Phase 3 migration removes the trigger before dropping the table.
Audit failure aborts the configuration transaction. Nonobject JSON input and
undeclared fields are rejected with HTTP 400.
