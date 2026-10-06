# MateMail Collaboration Features

Status: **Phase A + Phase B + Phase C + Phase D + Phase E + Phase F implemented on `feature/mail-collaboration`**.

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

## Phase F — Delegation

Delegation now gives one personal mailbox controlled access to another personal
mailbox without sharing or replacing either mailbox password.

### Management

Mail Hub has a dedicated **Delegation** surface. Organization owners/admins can:

- choose the personal mailbox being delegated;
- choose the personal mailbox receiving access;
- grant Read, Manage, Send As and/or Send on behalf;
- pause/reactivate a delegation;
- update permissions or remove the grant.

Self-delegation, cross-organization delegation, TeamBox targets and TeamBox
delegates are rejected. Manage still requires Read and an active delegation must
retain at least one permission.

### PostBox security boundary

The delegate always signs into PostBox with their **own** personal mailbox.

- `PostBoxSession.mailbox` remains the authenticated delegate identity.
- `PostBoxSession.active_mailbox` may point at the delegated personal mailbox.
- Delegated mailboxes appear in a dedicated **Delegated mailboxes** section,
  separate from TeamBoxes and from independently authenticated
  "Accounts on this device".
- Every request re-checks the live `delegation` grant server-side.
- Revoking the grant refuses the in-flight mailbox request and clears the stale
  active target instead of replaying the action against the delegate's own
  mailbox.
- Deleting a delegated target revokes sessions actively operating on it before
  the target row is removed.

Read-only delegates can view mailbox state without gaining Manage actions.
Send-only delegates get the same compose-only safety model as TeamBox users.
Shared Draft/Scheduled state still requires Manage + Send.

### Sending

Delegation reuses the generic Native Engine
`mailbox_sender_authorization` primitive introduced for TeamBox; no new engine
schema is required beyond schema v6.

For a personal target mailbox, `MailboxSpec.authorized_senders` is derived from
active Delegation grants that contain Send As or Send on behalf.

- **Send As** — the target mailbox (or one of its Aliases) is the visible
  sender, while SMTP authentication remains the delegate's personal mailbox.
- **Send on behalf** — the target mailbox is `From` and the delegate is the RFC
  `Sender`.
- The target remains directly login-capable with its original password.
- Removing delegated sending rights replaces the engine sender set without
  changing the target password or login state.
- An Alias targeting the delegated mailbox inherits the same effective delegate
  sender authorization through `postfix_sender_login`.

Forwarding and Forward Groups do not create Delegation rights.

### Push and audit

Push registrations for a delegated mailbox are valid only while the personal
session retains Delegation Read permission. Revoking Read stops future push
delivery for that delegate without affecting the mailbox owner's own devices.

Delegated immediate/scheduled sends already use the Phase D actor-aware audit
path, so the authenticated personal actor remains visible even though the
message belongs to the target mailbox.

## Phase E — Forward Groups (FG)

Forward Group is now a first-class distribution feature, deliberately separate
from both Mailbox Forwarding and Alias.

### Product behaviour

- A Forward Group has an address and display name but **no mailbox row, login,
  password, Inbox, Sent folder or storage**.
- At least one mailbox member is required. Members may be personal mailboxes or
  TeamBoxes.
- Each member receives its own delivery copy.
- Membership is de-duplicated structurally by a database uniqueness constraint.
- Member roles are `member` and `owner`; organization admins retain Hub-level
  administrative control.
- A Forward Group address is reserved through the same `AddressClaim` registry
  as Mailboxes, TeamBoxes and Aliases, so cross-feature address collisions are
  impossible.
- Forward Group addresses never appear in PostBox sender identities and never
  enter `postfix_sender_login`.

### Sender policies

Mail Hub exposes four posting policies:

- **Anyone** — Internet and authenticated senders may post.
- **Organization only** — any active personal mailbox in the organization.
- **Members only** — active personal mailboxes that are group members.
- **Selected senders** — explicit active personal mailboxes chosen by an admin.

MateMail resolves those product policies to concrete authenticated mailbox
addresses before sending the desired state to the engine.

The Native Engine publishes `postfix_forward_group_policy`, and Postfix calls
the loopback policy service at RCPT time. Restricted groups trust the
**authenticated SASL mailbox**, not the visible MAIL FROM address, so an
Internet sender cannot bypass "organization only" by forging an internal From.

### Native Engine routing

Engine schema v6 introduces dedicated `forward_group`,
`forward_group_destination` and `forward_group_sender` tables.

`postfix_virtual_alias` unions Forward Group destinations with Alias and
Forwarding routes because Postfix asks one delivery-routing question. The
product state remains separate in its own tables, and `postfix_sender_login`
never references Forward Groups.

The Forward Group desired-state operation is replace-not-merge and idempotent:
member and sender sets are completely reconciled on every sync.

### Loop protection

Nested Forward Groups are not introduced in Phase E. The practical direct loop
is therefore:

`group -> member mailbox -> forwarding -> same group`

MateMail blocks that shape in both directions:

- adding a member whose active Forwarding already points back to the group;
- creating or re-enabling Forwarding from a member back to its group.

Deleting the last group member is also refused. Mailbox/TeamBox deletion is
blocked when it would remove a group's last member, and mailbox lifecycle
changes reconcile the affected distribution/sender state.

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
- Shared Drafts and Scheduled messages require both Manage and Send permission;
  users without Manage cannot create mailbox state they cannot later control.
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
