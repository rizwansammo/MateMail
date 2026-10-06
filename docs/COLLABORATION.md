# MateMail Collaboration Features

Status: **Phase A foundation implemented on `feature/mail-collaboration`**.

This document defines the product vocabulary and security boundaries for the
next collaboration feature set. Later phases add customer surfaces; Phase A
only establishes the primitives they must share.

## Product vocabulary

| MateMail term | Meaning |
|---|---|
| Personal mailbox | A normal mailbox that authenticates directly to PostBox. |
| Alias | Another address for an existing mailbox identity. It is not a mailbox. |
| TeamBox | A stored shared mailbox. It has Inbox/Sent/Drafts/folders but no direct end-user login. |
| Forward Group (FG) | A distribution address that fans one message out to members. It has no Inbox, password or PostBox session. |
| Delegation | Explicit permission for one personal mailbox identity to access another personal mailbox. |
| Forwarding | The existing mailbox-to-destination forwarding rule. It remains separate from Forward Group. |

## Phase A invariants

### One address, one product object

A routable address may be owned by exactly one of Personal Mailbox, TeamBox,
Alias or Forward Group.

`mail_directory.AddressClaim` is the database serialization point. Existing
mailboxes and aliases are backfilled during migration. New Mailbox and Alias
rows reserve the address in the same transaction as their own creation.

The migration fails closed if historical data contains a case-insensitive
collision. It never chooses a winner automatically.

### PostBox authentication stays mailbox-bound

- A browser session is authenticated as exactly one personal mailbox.
- A Workspace login never implies permission to read mail.
- A TeamBox cannot sign in directly.
- Cross-mailbox access is represented by an explicit `MailboxAccessGrant`.

Later PostBox phases may let an authenticated personal mailbox select an
authorized target mailbox, but every request must resolve authorization
server-side. A client-supplied mailbox id is never authority by itself.

### Access permissions

`MailboxAccessGrant` supports two grant types:

- `team_box` — TeamBox membership.
- `delegation` — personal mailbox delegation.

Permissions are explicit: Read, Manage, Send As and Send on behalf. Manage
requires Read. Only a personal mailbox may be a grantee. TeamBox membership
must target a TeamBox; Delegation must target a personal mailbox. Cross-tenant
grants are rejected.

### Forwarding is not Forward Group

The existing `ForwardingRule` model is unchanged.

Forwarding means `one mailbox -> destination address(es)`.

A future Forward Group means `one group address -> managed group members`.

They may eventually share low-level Mail Engine routing primitives, but they
must not share MateMail product models, APIs or UI terminology.

## Planned phases

- **A** — shared-address registry, mailbox kind, access-grant foundation.
- **B** — normalize existing Alias behavior around mailbox identities.
- **C** — TeamBox Hub/backend/mail-engine management.
- **D** — TeamBox access inside PostBox.
- **E** — Forward Group (FG).
- **F** — Delegation.
- **G** — end-to-end security, migration and production hardening.
