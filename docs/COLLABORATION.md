# MateMail Collaboration Features

Status: **Phase A + Phase B + Phase C + Phase D implemented on `feature/mail-collaboration`**.

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

## Phase D — TeamBox access inside PostBox

A TeamBox is now usable from a member's ordinary PostBox login without ever
becoming a login identity itself.

### Session boundary

- `PostBoxSession.mailbox` remains the immutable authenticated personal
  mailbox.
- `PostBoxSession.active_mailbox` may select an authorised TeamBox.
- Every request re-resolves the live TeamBox grant server-side; a mailbox id
  supplied by the browser is never authority.
- PostBox exposes the active mailbox, authenticated personal mailbox, current
  permissions and available TeamBoxes separately.
- TeamBoxes and "Accounts on this device" remain separate UI concepts:
  TeamBox switching uses grants, while account switching uses independent
  authenticated sessions.

If TeamBox access is revoked or the TeamBox becomes unavailable while selected,
the stale selection is cleared. The current mailbox-content request is refused
rather than replayed against the personal mailbox. Recovery endpoints can then
continue with the still-valid personal session.

### Permission behaviour

- **Read** opens the shared Inbox, Sent, Drafts, folders, messages and
  conversation history.
- **Manage** permits shared state changes such as read/unread, star, labels,
  folders, move, archive, spam and delete. Read-only access does not mark mail
  read merely by opening it.
- **Send As** submits with the TeamBox address as the visible sender.
- **Send on behalf** uses the TeamBox as `From` and adds the authenticated
  personal mailbox as the RFC `Sender`.
- A send-only member gets a compose-only TeamBox surface and cannot read shared
  mail.
- Send-only access cannot create hidden Drafts or scheduled messages that the
  member would be unable to review or cancel.
- Shared TeamBox signatures may be read by members who can send; changing a
  shared signature requires Manage.

### Submission and audit

SMTP authentication always uses the signed-in personal mailbox. The message
identity and Sent copy belong to the TeamBox. This preserves Postfix's
sender-login enforcement while keeping the shared mailbox passwordless.

Scheduled TeamBox messages store the personal submission mailbox separately and
re-check authorization at send time. Removing the grant before delivery causes
the scheduled send to fail closed.

TeamBox immediate sends, schedules and executed scheduled sends record the
personal actor in the audit log. Deleting a TeamBox revokes sessions actively
using it before the mailbox row is removed.

## Phase C — TeamBox management

TeamBox is now a first-class shared-mailbox feature in Mail Hub.

- TeamBoxes are stored as `Mailbox(kind="team_box")` and use the same storage
  quota and mailbox-plan limit as personal mailboxes.
- A TeamBox has no direct password and cannot authenticate to PostBox.
- It remains a real mail recipient with Maildir storage and normal inbound
  delivery.
- Mail Hub has dedicated TeamBox list/detail screens instead of mixing TeamBoxes
  into the personal Mailboxes screen.
- Owner/Admin can create, enable/disable, reprovision and delete TeamBoxes.
- TeamBox members are personal mailboxes from the same organization.
- Per-member permissions are Read, Manage, Send As and Send on behalf.
- Manage requires Read and an active membership must retain at least one
  permission.
- Member add/update/remove is synchronized to the Mail Engine before the Hub
  treats the resulting sender rights as usable.
- TeamBox deletion is blocked while an Alias points to it, avoiding an orphaned
  engine Alias route.

### Native Mail Engine enforcement

Engine schema v5 separates delivery from authentication:

- `mailbox.login_enabled=false` keeps a TeamBox deliverable and present in
  Dovecot userdb while excluding it from direct Dovecot authentication.
- `mailbox_sender_authorization` maps a TeamBox to the personal mailboxes
  permitted to submit as that address.
- `postfix_sender_login` includes those mappings, so Send As / Send on behalf
  rights are enforced by SMTP sender-login checks as well as the Hub.
- Aliases that target a TeamBox inherit the TeamBox's effective authorised
  senders.
- Forwarding is still absent from sender authorization by construction.

Phase D will expose the already-authorized TeamBox inside a member's PostBox
session. Phase C deliberately does not add mailbox switching to PostBox.

## Phase B — Alias is a mailbox identity

MateMail now defines an Alias as an alternate address for exactly one existing
MateMail mailbox.

- Alias creation requires `destination_mailbox_id`.
- The destination mailbox must belong to the same organization.
- The old `destination_address` API shape is rejected with guidance to use
  Forwarding for external delivery.
- The database no longer stores an arbitrary Alias destination address.
- PostBox Send As identities come only from Alias rows linked by mailbox FK.
- Mail Hub no longer offers an external destination option when creating an
  Alias.
- Platform oversight treats Alias destinations as structurally internal.

The migration converts a legacy literal destination only when it already names
a mailbox in the same organization. Any genuinely external legacy Alias makes
the migration fail closed rather than silently changing mail flow.

The existing `ForwardingRule` model/API/UI is unchanged.

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
