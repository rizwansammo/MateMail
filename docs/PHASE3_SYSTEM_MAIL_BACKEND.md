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

## Phase 3 Read-only Integration

New Platform Admin endpoints:

- `GET /api/platform/system-mail/readiness/`
- `GET /api/platform/system-mail/history/`

Readiness uses **only** the standard Native Engine adapter's
`list_domains` and `list_mailboxes` operations, scoped to
`mail.matemail.pro` and `system.matemail.pro`. Other customer names and
arbitrary mailbox addresses are not returned. In stub mode or whenever the
Native Engine cannot be inspected, the status is `unknown`, never healthy by
assumption. Engine errors and connection secrets are not serialised.

The DNS, signing, SMTP/IMAP and credentials gates remain expressly unverified
until live dedicated validation. The readiness API always returns
`activation_supported:false` and `cutover_ready:false`: a draft or even
an active system domain in a read-only inventory never authorises cutover.

The history endpoint returns at most twenty immutable proposed revisions,
without credentials or secrets. Tenant/API key access remains prohibited.
