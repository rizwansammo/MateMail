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

## Operational alignment and provisioning preview

The read-only readiness response now classifies the *actual configured*
transactional sender, DMARC receiver and TLS receiver as `legacy`, `target`,
`other` or `unset`. A submitted draft never affects this classification.

The `provisioning_preview` derives future actions from native read-only
inventory: `present`, `would_need_activation`,
`would_need_provisioning` or `unknown`. It **never** invokes
`ensure_domain`, `ensure_mailbox` or any other Native Engine mutation.
Neither the preview nor the inventory can assert DNS, authentication or live
message delivery readiness.

Phase 3 does not add an authorized system mailbox mutation or dynamic sender
activation endpoint. That authorization and its controlled cutover must be
explicitly designed and verified before Phase 5. The current sender and
receivers remain entirely driven by deployment environment configuration.

## Secure inactive Native resource staging (default OFF)

An explicitly confirmed platform-only staging command exists in the feature
branch. This is NOT part of normal tenant provisioning and is not an automatic
side effect of saving a configuration draft.

- Django: `SYSTEM_MAIL_STAGING_ENABLED=False` and
  `NATIVE_ENGINE_PLATFORM_SECRET` (empty by default).
- Native Engine: `NATIVE_PLATFORM_OPERATIONS_ENABLED=False`,
  `NATIVE_PLATFORM_SECRET` and `NATIVE_PLATFORM_DB_PASSWORD` (empty by
  default). Platform and ordinary API/DB secrets must be independent.
- Native migration 008 provisions the dedicated
  `engine_platform_operator` PostgreSQL principal in NOLOGIN state.
- When explicitly gated and authenticated, the database function inserts
  only an inactive system domain and five disabled/passwordless system mailbox
  placeholders. It does not create credentials or a DKIM key.
- An audit record precedes the outbound command and another reports either
  confirmed or unknown outcome. Never infer "no mutation" from a timeout.

See `docs/PHASE3_NATIVE_STAGING_QA_20261013.md`. Native DB superuser
hardening and full end-to-end integration are still open release gates.
