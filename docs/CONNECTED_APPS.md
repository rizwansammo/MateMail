# MateMail Connected Apps

MateMail Connected Apps lets an external CRM, helpdesk, ticketing system, ERP,
or other trusted application use one exact mailbox without receiving that
mailbox's password or the PostBox master credential.

## Connection flow

1. A MateMail workspace owner/admin opens **Settings -> Connected Apps**.
2. They enter an application name, choose the purpose, choose one mailbox, and
   approve only the access the application needs.
3. MateMail shows a **Tenant ID** and one-time **Integration Secret**.
4. The external application sends those two values to
   `POST /api/integrations/connect/start/`.
5. MateMail returns a short-lived authorization URL and polling token.
6. The application opens the authorization URL in MateMail.
7. MateMail requires a fresh workspace password and the workspace user's 2FA
   when enabled. The mailbox-level verification hook is also evaluated, so a
   future mailbox 2FA feature can be added without changing the external app.
8. After approval the external application polls
   `POST /api/integrations/connect/status/` exactly once to receive a
   revocable operational access token.
9. The Integration Secret is no longer needed by the application.

The operational token is always bound to the original MateMail tenant,
integration and mailbox.

## Scopes

The Portal shows human language. API responses also include stable scope keys.

| Scope | Human meaning | Typical use |
| --- | --- | --- |
| `mailbox.read` | See this mailbox's address and basic details | Every connected application |
| `mail.send` | Send email from this mailbox | CRM, helpdesk, ERP |
| `mail.read` | Read email in this mailbox | Helpdesk, ticket ingestion |
| `mail.modify` | Mark email as read or unread | Helpdesk polling acknowledgement |
| `signatures.read` | Use this mailbox's signatures | CRM / sales tools |

Connected Apps do **not** expose domain administration, mailbox password
management, tenant administration, message deletion or the PostBox master
credential.

## Purpose presets

Purpose only selects a safe default scope set. The admin can adjust it before
creating the connected app.

- **Sales / CRM**: mailbox details, send email, signatures.
- **Helpdesk / Ticketing**: mailbox details, read email, mark read/unread, send email.
- **Custom application**: mailbox details only by default.

## Operational API

Every operational request uses:

- `Authorization: Bearer mmc_...`
- `X-MateMail-Tenant: <tenant UUID>`

Endpoints:

- `GET /api/integrations/external/profile/`
- `GET /api/integrations/external/folders/`
- `GET /api/integrations/external/messages/`
- `GET /api/integrations/external/messages/detail/`
- `GET /api/integrations/external/messages/raw/`
- `POST /api/integrations/external/messages/seen/`
- `GET /api/integrations/external/signatures/`
- `POST /api/integrations/external/send/`
- `POST /api/integrations/external/disconnect/`

All endpoints re-check the current tenant/mailbox state and the granted scope.
Revoking the connected app in MateMail revokes its operational tokens.
