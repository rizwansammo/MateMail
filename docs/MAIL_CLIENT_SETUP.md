# MateMail — Mail Client Settings

Written for whoever configures a mail client, and for support answering "what
do I put in these boxes?".

---

## Settings

| | Incoming | Outgoing |
|---|---|---|
| Protocol | IMAP | SMTP |
| Server | `mx.matemail.online` | `mx.matemail.online` |
| Port | **993** | **587** |
| Encryption | **SSL/TLS** (from connect) | **STARTTLS** |
| Authentication | Normal password | Normal password, **required** |
| Username | the full email address | the full email address |

The username is always the whole address — `you@yourdomain.com`, not `you`.
Outgoing mail requires authentication; there is no "no authentication" option
and a client configured that way will be refused.

---

## What the server will and will not accept

**Supported**

- IMAP over TLS on 993
- SMTP submission with STARTTLS on 587
- TLS 1.2 and TLS 1.3

**Not offered, deliberately**

| | Why |
|---|---|
| POP3 (110) and POP3S (995) | Not offered. IMAP keeps mail on the server, which is what every current client expects. |
| Plaintext IMAP (143) | Not merely closed — it does not listen at all. A password must never cross a network unencrypted. |
| Implicit TLS submission (465) | 587 with STARTTLS covers every current client. 465 may be reconsidered if real client data shows a need (DEC-042). |
| TLS 1.0 / 1.1 | Withdrawn. No client that can reach a 2026 mail server is limited to them. |

A client that insists on 465 or POP3 will not connect. That is the
configuration being wrong, not the server being down.

---

## Certificate

`mx.matemail.online`, issued by Let's Encrypt, valid for that exact name.
Clients validate it normally — there is nothing to accept manually, and a
client prompting to trust an unknown certificate means it is not talking to
this server.

Renewal is automatic and installs into both the SMTP and IMAP services.

---

## If sending fails

| Symptom | Cause |
|---|---|
| "Authentication failed" | Username must be the full email address. |
| "Must issue a STARTTLS command first" | The client is set to no encryption on 587. |
| "Sender address rejected: not owned by user" | The From address does not belong to the account that logged in. A mailbox may send as itself and as identities it has been granted, not as anything else. |
| "Relay access denied" | The client is not authenticating. Outgoing mail requires it. |
| Repeated failures then nothing | Too many wrong passwords in a short time will temporarily block the address for an hour. Fix the password and wait, or ask an operator. |

---

## For operators

Public ports and the reasoning behind them: `docs/NATIVE_MAIL_ENGINE.md`.
Abuse protection, including how to release a blocked address:
`docs/SECURITY.md`.

Automatic client configuration (Thunderbird autoconfig, Outlook autodiscover)
is not implemented. It is a product feature for later, not a prerequisite:
every client accepts the settings above typed in by hand.
