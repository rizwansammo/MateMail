> **CURRENT DOCUMENTATION NOTICE (2026-10-09):** Acceptance checklist from the old hosting period. The current Autodiscover endpoint is `autodiscover.matemail.pro` and mail client server is `mx.matemail.pro`; historical test outcomes below are not evidence of current Outlook compatibility.
> Authoritative current references: [Architecture](ARCHITECTURE.md),
> [Deployment](DEPLOYMENT.md), and [Backup/Azure DR](BACKUP_RESTORE.md).
> Sections below may describe historical migration states or retired domains.

---

# Outlook acceptance — what has actually been observed

Written for whoever tests a real Outlook client against MateMail, and for
support and sales answering "does it work with Outlook?".

---

## The honest position, today

**MateMail supports Outlook through IMAP and authenticated SMTP.** Those
settings are in `MAIL_CLIENT_SETUP.md` and they work in every client that
accepts typed-in IMAP settings.

**MateMail provides an Autodiscover compatibility endpoint** at
`autodiscover.matemail.pro`, which returns IMAP and SMTP settings to clients
that ask for them.

**No Outlook build is currently claimed to configure automatically.** Not one
row in the table below has been filled in by observation. Until a row is
filled in from a real client, the answer to "will Outlook set itself up?" is
"we do not know yet, and some versions will need manual setup".

This is not hedging. Recent Outlook builds increasingly steer generic IMAP
accounts through Microsoft-hosted setup flows and may ignore a third-party
Autodiscover response entirely. That is client behaviour, and the response to
it is manual/Advanced IMAP setup — **not** Exchange emulation, which MateMail
will not implement (DEC-057).

---

## Why automated tests are not enough

`backend/tests/test_autodiscover.py` proves the document is correct: right
namespaces, right ports, STARTTLS described as STARTTLS, no protocol we do not
serve, no mailbox disclosure. All of that can be true of a response that a
given Outlook build never requests, or requests and then ignores.

The only evidence that a client configures automatically is watching it do so.

---

## Before testing

The endpoint must be live. Until it is deployed, every client below will fall
through to manual setup and the test proves nothing:

- [ ] `autodiscover.matemail.pro` A record resolves
- [ ] certificate issued and valid for that name
- [ ] nginx vhost installed, `nginx -t` clean
- [ ] `curl -sS -X POST https://autodiscover.matemail.pro/autodiscover/autodiscover.xml
      -H 'Content-Type: text/xml' --data @request.xml` returns an IMAP block
- [ ] the test domain's `_autodiscover._tcp` SRV record is published and resolves
- [ ] a real mailbox exists on that domain, with a known password

A minimal `request.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<Autodiscover xmlns="http://schemas.microsoft.com/exchange/autodiscover/outlook/requestschema/2006">
  <Request>
    <EMailAddress>you@your-test-domain.example</EMailAddress>
    <AcceptableResponseSchema>http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a</AcceptableResponseSchema>
  </Request>
</Autodiscover>
```

---

## Per-client checklist

Run the whole list for each client. Record what happened, including "no" —
a negative result is the useful one here, because it is what support needs.

For each of:

1. **Classic Outlook for Windows** (Microsoft 365 / Office desktop)
2. **New Outlook for Windows**
3. **Outlook for Android**
4. **Outlook for iOS**

record:

| # | Question | How to tell |
|---|----------|-------------|
| 1 | Did it query the SRV record? | DNS query log, or `tcpdump`/Wireshark on port 53 during setup |
| 2 | Did it request the Autodiscover endpoint? | nginx access log on the autodiscover vhost |
| 3 | Did it consume the IMAP settings? | account shows server `mx.matemail.pro` port 993 without typing them |
| 4 | Did it consume the SMTP settings? | outgoing shows port 587 STARTTLS without typing them |
| 5 | Did it still require Advanced/manual setup? | whether you had to choose IMAP yourself |
| 6 | Did authentication succeed? | account added without a repeated password prompt |
| 7 | Does the Inbox sync? | existing messages appear |
| 8 | Do folders sync? | Sent, Drafts, Trash, Junk and any custom folder appear |
| 9 | Can it send? | send to an external address and confirm arrival |
| 10 | Does the sent copy land in Sent? | check from PostBox, not only from Outlook |
| 11 | Does a reply arrive back? | reply from the external address |

### Results

Fill in from observation only. Leave a row blank rather than guessing.

| Client | Version | Queried SRV | Hit endpoint | Used IMAP | Used SMTP | Manual needed | Auth | Inbox | Folders | Send | Sent copy | Reply |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Classic Outlook (Windows) | | | | | | | | | | | | |
| New Outlook (Windows) | | | | | | | | | | | | |
| Outlook (Android) | | | | | | | | | | | | |
| Outlook (iOS) | | | | | | | | | | | | |

---

## Reading the results

**If a client never hit the endpoint** (row 2 is "no"), Autodiscover is not the
problem and no change to the XML will help. Either it never queried the SRV
record, or it resolved the settings from Microsoft's own directory first. Note
it and move on.

**If it hit the endpoint but ignored the settings** (2 yes, 3 and 4 no), capture
the exact response body from the access log and compare it against
`test_autodiscover.py`. If the document is correct and the client still ignores
it, that is the client's behaviour. Record it.

**If it consumed the settings but authentication failed**, the problem is not
discovery. Check the username is the full address, and check Dovecot's log.

**Under no circumstances** does a failure here justify emitting an Exchange,
MAPI, EWS or ActiveSync block. MateMail does not serve those protocols, and
advertising one moves the failure later — after the user has typed a password —
rather than removing it.

---

## What to say to customers meanwhile

> MateMail works with Outlook using IMAP and authenticated SMTP. Some Outlook
> versions do not configure IMAP accounts automatically and need Advanced or
> manual setup: choose IMAP rather than letting Outlook pick, then enter the
> servers and ports from your mailbox settings.

Do not say "Outlook is supported automatically", and do not say "Outlook is not
supported". Both are wrong.
