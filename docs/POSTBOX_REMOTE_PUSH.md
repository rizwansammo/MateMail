# POSTBOX_REMOTE_PUSH.md — Native PostBox remote push (server half)

**Status:** server half **built and tested locally. Not deployed.** No Firebase
project, no Microsoft Entra ID app registration and no provider credential
exists, so no real push has ever been sent. Every FCM and WNS call in the test
suite goes to mocked HTTP. The PostBox-App client half is **not built**; its
contract is the last section of this document.

**Owner:** MateMail backend + Native Engine · **Decision:** DEC-058 ·
**Last updated:** 2026-09-26

---

## 1. What this is

When mail is delivered to a mailbox, MateMail tells that mailbox's PostBox
apps on Android (FCM) and Windows (WNS) that a message arrived, even when the
app is not running. The push is a **wake-up carrying identifiers only**. The
app then fetches what it needs through the authenticated PostBox API and
decides for itself whether to show a notification.

It is not a mail path. The rule every part of it is built around:

> **Push failure must never cause email delivery failure.** If Django, Celery,
> FCM or WNS is down, if a credential is wrong, if a device token is dead, the
> message is still delivered to Dovecot, exactly as it would have been without
> any of this.

Not included, on purpose: iOS/APNs, Web Push, message-content previews,
WebSocket/SSE substitutes, any change to web PostBox notifications, and any
mailbox polling, IMAP polling, Maildir watching or mail-log parsing.

---

## 2. The path

```
Postfix ──LMTP──► Dovecot 2.4.1 (matemail-native-dovecot)
                    │ saves + COMMITS the message          ◄── mail is delivered here
                    │ push_notification (Lua driver), LMTP only
                    │ postbox-push.lua: one POST, 1 s limit, after the commit
                    ▼  engine network only (172.27.0.0/16)
                  Native API  POST /v1/dovecot/push     X-Native-Push-Secret
                    │ validate 4 fields → event_id (UUIDv5) → bounded queue
                    │ answers 202 at once; relay thread does the rest
                    ▼  matemail_engine_link (internal, no egress)
                  MateMail  POST /api/internal/postbox/push-events/   X-PostBox-Push-Secret
                    │ store once (event_id is the primary key) → on commit, queue
                    ▼  Celery
                  dispatch_push_event → claim → one send_push per active device
                    ▼
                  FCM HTTP v1 (Android)  /  WNS raw (Windows)  →  PostBox app
```

Nothing on this path reads mail. There is no IMAP, no polling and no content:
an event names a saved message by mailbox, folder, UIDVALIDITY and UID, and
holds nothing of it.

Dovecot never talks to MateMail. It talks to the engine API, on the engine
network, as its existing auth-policy hook already does
(`http://api:8451/v1/dovecot/policy/`). Only the API, which was already on
`matemail_engine_link`, carries the event across. No port is published, no
network membership changes, and Dovecot gains no database or MateMail
credential.

---

## 3. The Dovecot mechanism, and the evidence for it

The mechanism was chosen from the pinned image, not from memory:
`dovecot/dovecot:2.4.1@sha256:1296e0f1029cdd95e6849fb82f5d142a6e2a46218451773316cea678de75254b`.

**What the image ships** (inspected in an exported copy of the image):
`lib20_push_notification_plugin.so`, `lib22_push_notification_lua_plugin.so`,
`lib01_mail_lua_plugin.so`, `libdovecot-lua.so` (with Dovecot's HTTP client
bound for Lua), Lua 5.3 and the `json` module. Pigeonhole is present but not
configured.

**Why this one.** `push_notification` is Dovecot's own post-commit hook, built
for exactly this job. It runs inside the delivering process after the save has
committed, it is given the UID and UIDVALIDITY the commit assigned, and it
fires only for the protocols it is loaded into. Everything else was rejected:

| Alternative | Why not |
|---|---|
| Maildir watcher, `tail` of the mail log, cron scanner, IMAP polling or IDLE fan-out | Explicitly out of scope, and each is fragile (races, log format changes, fan-out cost) |
| A Postfix content filter or milter | Runs before Dovecot has the message, so it cannot know the UID and would announce mail that may still fail to deliver |
| Sieve `enotify` or `vnd.dovecot.execute` | Needs Pigeonhole configured and extprograms enabled, runs before the save commits, and would execute programs inside LMTP |

**Configuration** (rendered by `images/dovecot/entrypoint.sh` into
`/etc/dovecot/engine-push.conf`, mode 0600 root, included with `!include_try`):

```
protocol lmtp {
  mail_plugins {
    notify = yes
    push_notification = yes
    mail_lua = yes
    push_notification_lua = yes
  }
}
push_notification postbox {
  push_notification_driver = lua
  lua_file = /etc/dovecot/postbox-push.lua
  lua_settings {
    url = http://api:8451/v1/dovecot/push
    secret = <NATIVE_DOVECOT_PUSH_SECRET>
  }
}
```

Without `NATIVE_DOVECOT_PUSH_SECRET` the rendered file is a single comment:
no plugin loads and LMTP is exactly what it was. A secret or URL containing a
character that cannot be represented in a Dovecot value makes the entrypoint
exit 78, and the value is never echoed.

**The script** (`dovecot/postbox-push.lua`) collects one entry per MessageNew
in `dovecot_lua_notify_event_message_new`, and sends only from
`dovecot_lua_notify_end_txn(ctx, success)` when `success` is true. It reads
exactly `event.mailbox`, `event.uid_validity` and `event.uid`, plus
`user.username`. Dovecot also hands the script the sender, recipients, subject
and a body snippet; none of those is read, and a test fails if one ever is.
Each event is one POST with `request_absolute_timeout = 1s`,
`request_max_attempts = 1` and `request_max_redirects = 0`, inside `pcall`.
A failure is one warning line.

### Measured against the real image

All with the MateMail derivative of the pinned image, locally, synthetic
mailboxes only:

| Case | LMTP answer | Time | Message saved | Push |
|---|---|---|---|---|
| Relay target up | 250 | 0.07–0.41 s | yes | exactly `{mailbox, folder, uid_validity, uid}`, correct secret header, JSON |
| Relay target stopped | 250 | 1.08 s | yes | none; `Warning: postbox-push: engine API not reached (Dovecot HTTP client status 9008)` |
| Relay target accepts and never answers | 250 | released at 1.08 s | yes | none; one warning |
| `doveadm save` (not a delivery) | n/a | n/a | yes | none (plugins are LMTP-only) |
| UID/UIDVALIDITY in the event vs `doveadm mailbox status` | match for every delivery | | | |
| Subject/body markers of the test messages | never reached the receiver | | | |

The production configuration itself was also started in the image, with the
real entrypoint and dummy values, and inspected with `doveconf -n`:

| Case | Result |
|---|---|
| Production `dovecot.conf` + new entrypoint, push secret set | starts for imap and lmtp; push section present; `driver = lua` |
| Same, push secret unset | starts; include is a comment; no push section |
| Production `dovecot.conf` + a Dovecot image from before this change | with `!include`: **Fatal, IMAP and LMTP down**. With `!include_try`: starts, no push |
| Plugins actually loaded by an LMTP delivery (`mail_debug`) | mail_lua, **quota**, notify, push_notification, push_notification_lua |
| `engine-push.conf` | 0600 root:root |

Two findings changed the code:

- **`!include_try`, not `!include`.** `dovecot.conf` is config and ships with
  `git pull`, but the entrypoint that renders the include ships in the image. A
  plain `!include` turns "config deployed before the image" into a Dovecot
  that will not start. Push is optional, so a missing include now costs the
  push and nothing else.
- **Quota stays loaded.** `doveconf -f protocol=lmtp mail_plugins` prints only
  the filter's own four entries, which looks as if quota was dropped for LMTP.
  It was not: 2.4's boolean list adds to the global one, and the debug log of a
  real delivery shows quota loaded. A test pins the boolean-list form.

**Folder accuracy.** `folder` is the mailbox Dovecot saved to. This engine
configures no Sieve, no detail-mailbox delivery and no recipient delimiter, so
today every delivery is saved to `INBOX`. PostBox rules compile to Sieve
(DEC-052), but Sieve has never run on the Native Engine. If it is enabled, the
folder a `fileinto` rule produces must be measured before anyone relies on it.
Until then, nothing here claims more than "the folder Dovecot reported".

---

## 4. Native Engine API: `POST /v1/dovecot/push`

`engine/native_api/push.py`, routed in `app.py` **before**, and independently
of, the provisioning secret check.

| | |
|---|---|
| Auth | `X-Native-Push-Secret` = `NATIVE_DOVECOT_PUSH_SECRET`, compared with `hmac.compare_digest`. Unset refuses every report. This credential opens this endpoint and nothing else. |
| Body | exactly `mailbox`, `folder`, `uid_validity`, `uid`. Anything else, missing or extra (a subject, a snippet), is refused with 400, not trimmed. |
| Validation | `mailbox` is an address (lower-cased); `folder` 1–255 chars, no control characters; UIDs are integers 1–4294967295 (booleans and strings refused) |
| Answer | `202 {"queued": true|false}` immediately. Dovecot ignores the answer, because the mail was saved before it asked. |

**Event id.** `uuid5(NAMESPACE, "new_mail\n{mailbox}\n{folder}\n{uid_validity}\n{uid}")`
with `NAMESPACE = uuid5(NAMESPACE_URL, "https://matemail.online/ns/postbox-new-mail")`.
The same saved message always gets the same id, so a repeated report or a
relay retry is the same event to MateMail. A new UIDVALIDITY (a recreated
folder) gives new ids. **The namespace must never change**, or every past
delivery would look new.

**Relay.** A background thread; `submit` never blocks and never raises.

| | |
|---|---|
| Target | `NATIVE_POSTBOX_PUSH_URL` (e.g. `http://backend:8000/api/internal/postbox/push-events/`) with `X-PostBox-Push-Secret: NATIVE_POSTBOX_PUSH_SECRET`. Either unset disables the relay. |
| Queue | bounded at 1000. When full, the event is dropped with a warning. It never grows without limit. |
| Request | 3 s timeout; **no redirects followed; no proxy** (an explicit empty `ProxyHandler`), so the credential cannot be sent anywhere but the configured URL |
| Retries | two more attempts, after 2 s and 5 s, only when MateMail did not answer (connection failure, 408, 429, 5xx). A refusal (403, 400, 404, 409, 413) is not retried. |

---

## 5. MateMail ingest: `POST /api/internal/postbox/push-events/`

`apps/postbox/views_push.py` → `PushEventIngestView`. Under `/api/internal/`,
which host nginx denies at the edge. Reached only from the engine API over
`matemail_engine_link`.

Checks, in this order, so an unauthenticated caller learns nothing about any
mailbox:

1. `X-PostBox-Push-Secret` = `POSTBOX_PUSH_INGEST_SECRET`, compared in
   constant time. Unset refuses everything → **403** `{"detail": "Forbidden."}`.
   It is its own secret: never `INTERNAL_API_SECRET`.
2. Body ≤ 4096 bytes → otherwise **413**.
3. Strict fields: `event_id` (UUID), `event` (`new_mail`), `mailbox`,
   `folder`, and `uid_validity` + `uid` (both or neither, 1–2³²−1). Unknown
   fields or control characters → **400**.
4. The mailbox must exist and be allowed to sign in (active, provisioned,
   organization may use mail) → otherwise **404** `{"detail": "Unknown mailbox."}`,
   the same answer for unknown and suspended.
5. `get_or_create` on `event_id`. The same id for a different mailbox →
   **409** (refused, never merged).

Answer: **202** `{"accepted": true, "event_id": "<uuid>", "duplicate": <bool>}`.

It never calls FCM, WNS or IMAP. The dispatch is queued with
`transaction.on_commit`. If the broker is unreachable, that is caught, logged
and left to the sweep: the row is already stored as PENDING.

---

## 6. Devices

`PostBoxPushDevice` (`postbox_push_device`) holds one installation's
registration for **one mailbox**:

| Field | |
|---|---|
| `id` | UUID, the registration id the app stores and receives in every push |
| `mailbox`, `session` | owner; both `CASCADE` |
| `installation_id` | UUID generated by the app, once per installation |
| `platform` / `provider` | `android`/`fcm` or `windows`/`wns` (no other pairs) |
| `token_type` | `registration_token` or `fid` (FCM), `channel_uri` (WNS) |
| `token` | the FCM token or WNS channel URI. **Written, never read back.** |
| `enabled`, `disabled_reason`, `disabled_at` | set by a provider's "invalid registration" answer |
| `last_seen_at`, `last_push_at`, `last_error` | diagnostics; codes only, never provider text |

Unique on `(mailbox, installation_id, provider)`, so re-registering updates one
row. At most **20** registrations per mailbox (409 beyond).

**Ownership follows the session.** A registration belongs to the PostBox
session that last registered it. Re-registering moves it to the current
session. It receives push only while:

- it is `enabled`;
- its session is neither revoked nor expired;
- the mailbox may still sign in.

All three are checked at dispatch, and again at send time, because a retry can
run minutes later.

**Revocation deletes the registration.** Signing out, "sign out everywhere", a
password change (which revokes the other sessions) and any other
`PostBoxSession.revoke()` / `revoke_other_sessions()` delete that session's
registrations at once. A dead session's provider tokens are not kept.
An *expired* session's registrations are never selected, and are deleted with
the session row by the existing prune, a week after expiry.

`PostBoxPreference.notify_in_app` is **not** a push switch and is not read.
The switch is the registration itself: register to turn push on, `DELETE` to
turn it off. What a notification shows is decided on the device.

---

## 7. Dispatch, delivery and retries

`PostBoxPushEvent` (`postbox_push_event`) is the event row: `event_id` (primary
key), mailbox, `event_type`, `folder`, `uid_validity`, `uid`, state
(`pending` → `dispatched` | `expired`), device count and timestamps. There is
no content column of any kind.

| Stage | Behaviour |
|---|---|
| `postbox.dispatch_push_event` | Loads the event. If it is older than **30 min**, marks it `expired` (a push about hour-old mail is noise, and the app syncs on launch). Otherwise **claims** it with a conditional `UPDATE pending→dispatched` (only one worker can fan out), re-checks the mailbox, and queues one `send_push` per active device. |
| `postbox.send_push` | Re-checks the registration, its session and the mailbox, calls the provider, and records the outcome. Retries **only** a transient outcome, at most **3** times: 30 s, 60 s, 120 s, or the provider's `Retry-After` if longer, capped at 15 min. |
| `postbox.sweep_push_events` (beat, every minute) | Re-queues `pending` events older than 60 s (a broker outage at ingest). Expires `pending` events older than 30 min. Reads the event table only: server event retry, not mailbox polling. |
| `postbox.prune_push_events` (beat, 04:20) | Deletes events older than **7 days** |

**Outcomes** (`PushOutcome`):

| Outcome | Examples | Result |
|---|---|---|
| `delivered` | FCM 200, WNS 200 | `last_push_at` set |
| `invalid_device` | FCM 404/`UNREGISTERED`, FCM 400/`INVALID_ARGUMENT`; WNS 404, 410; a channel off `notify.windows.com` | registration **disabled** (`enabled=false`, reason, time), never retried |
| `retry` | network error, FCM 429/5xx, WNS 406/5xx | bounded Celery retry |
| `rejected` | FCM 401 after one token refresh, 403 (e.g. `SENDER_ID_MISMATCH`); WNS 400/403/405/413, 401 after refresh | not retried; the registration is **left alone**, because a MateMail configuration fault would otherwise disable every device |
| `unavailable` | provider not enabled/configured, unusable credentials | nothing sent, nothing disabled |

FCM's `INVALID_ARGUMENT` is treated as the registration's fault only because
FCM documents it so "if the payload is completely valid". Ours is fixed and
covered by tests.

**Idempotency, end to end.** Deterministic `event_id` at the engine → primary
key at ingest (a repeat is `duplicate: true` and not queued again) → claim at
dispatch (the sweep and the normal path cannot both fan out) → the same
`event_id` in every send and retry. Delivery is at-least-once: a crash between
the provider accepting and the outcome being recorded can resend. The app
drops repeats by `event_id`.

---

## 8. Providers

### FCM (Android): `apps/postbox/push_providers.py` → `FcmPushProvider`

- `POST https://fcm.googleapis.com/v1/projects/{POSTBOX_FCM_PROJECT_ID}/messages:send`
- OAuth access token from the service-account JSON at
  `POSTBOX_FCM_CREDENTIALS_FILE`, scope
  `https://www.googleapis.com/auth/firebase.messaging`, through
  **google-auth 2.58.1**. No OAuth or JWT code is written here, and the
  Firebase Admin SDK is not used, since one HTTP call does not justify it.
  Refreshed once on a 401.
- A **data-only** message, high priority, TTL 3600 s. The target is
  `message.token` for `registration_token` and `message.fid` for `fid`. FCM
  marks `token` deprecated in favour of `fid`, and accepts an installation ID
  in `token` during the transition.
- Verified against the FCM HTTP v1 discovery document and "Manage FCM
  registration tokens" (fetched 2026-09-26). Tested with mocked HTTP only.

### WNS (Windows): `WnsPushProvider`

- Access token: Microsoft Entra ID client credentials,
  `POST https://login.microsoftonline.com/{POSTBOX_WNS_TENANT_ID}/oauth2/v2.0/token`,
  scope `https://wns.windows.com/.default`. Cached for `expires_in` − 5 min,
  and refreshed once on a 401.
- A **raw** notification to the channel URI:
  - `Content-Type: application/octet-stream`
  - `X-WNS-Type: wns/raw`
  - `X-WNS-Cache-Policy: cache` (one raw push is kept for an offline device)
  - `X-WNS-TTL: 3600`
  - the body is the payload as compact JSON, at most 5000 bytes
- The channel must be HTTPS on `notify.windows.com` or a subdomain, with no
  userinfo and port 443. This is checked at registration **and again before
  every send**, because that is the line that sends a bearer token to that
  host. Nothing is fetched to validate a channel, and redirects are never
  followed.
- The legacy Package-SID flow (`login.live.com`) is UWP-only and, per
  Microsoft, not compatible with Windows App SDK push, so it is not
  implemented.
- Verified against Microsoft Learn: the WNS overview, the Windows App SDK push
  quickstart, and "Push notification service request and response headers".
  Tested with mocked HTTP only.

---

## 9. Credentials and configuration boundary

| Value | Lives in | Held by | Opens |
|---|---|---|---|
| `NATIVE_DOVECOT_PUSH_SECRET` | engine `.env` | Dovecot (rendered into 0600 `engine-push.conf`) and the Native API | `/v1/dovecot/push` only |
| `NATIVE_POSTBOX_PUSH_URL` | engine `.env` | Native API | where the relay posts |
| `NATIVE_POSTBOX_PUSH_SECRET` = `POSTBOX_PUSH_INGEST_SECRET` | engine `.env` / MateMail `.env` | Native API / MateMail backend | `/api/internal/postbox/push-events/` only |
| `POSTBOX_FCM_ENABLED`, `_PROJECT_ID`, `_CREDENTIALS_FILE` | MateMail `.env`; the JSON is a mounted file outside Git | backend, celery-worker | FCM send for that project |
| `POSTBOX_WNS_ENABLED`, `_TENANT_ID`, `_CLIENT_ID`, `_CLIENT_SECRET` | MateMail `.env` | backend, celery-worker | WNS for that Entra ID app |

Three push credentials, three names, none of them the provisioning,
policy or internal secret. Dovecot holds exactly one: it can, at worst, report
a fake delivery, which produces a content-free wake-up and never provisions
anything. Everything is optional and defaults empty/`False`. Templates carry
placeholders only (`.env.example`, `deploy/env.production.example`,
`deploy/native-engine/.env.example`, both Compose files).

### Enabling, in order

1. **MateMail:** generate `POSTBOX_PUSH_INGEST_SECRET`, deploy. Reports can now
   be accepted. Devices can register at any time, with or without it.
2. **Engine:** set `NATIVE_POSTBOX_PUSH_URL` and
   `NATIVE_POSTBOX_PUSH_SECRET` (the same value), and generate a separate
   `NATIVE_DOVECOT_PUSH_SECRET`. This needs the **new API and Dovecot
   images**: the code and the entrypoint live in them, not in bind mounts.
   Deploy with `./deploy.sh`.
3. **Providers**, each independently:
   - FCM: a Firebase project, a service account allowed to send, its JSON
     mounted read-only into backend and celery-worker, then
     `POSTBOX_FCM_ENABLED=True` with the project id and file path.
   - WNS: an Entra ID app registration, and Microsoft's mapping of the app's
     Package Family Name to that Azure AppId, requested by email per the
     Windows App SDK quickstart. Then `POSTBOX_WNS_ENABLED=True` with the
     tenant, client id and secret.

None of this can break mail if done out of order. The worst case at each
step is a warning line and no push.

**Disabling:** unset `NATIVE_DOVECOT_PUSH_SECRET` and redeploy Dovecot. The
include becomes a comment and no push plugin loads.

### Log lines (all content-free)

| Where | Line |
|---|---|
| Dovecot | `postbox-push: engine API not reached (Dovecot HTTP client status 9008)` · `postbox-push: engine API answered HTTP <n>` · `postbox-push: new-mail event not sent: <lua error>` (Dovecot's own LMTP prefix names the recipient, as on every delivery line) |
| Native API | `PostBox push relay enabled` · the startup warning when Dovecot may report but the relay is unconfigured · `relay queue is full; event <id> dropped` · `MateMail answered <n> for event <id>` · `MateMail did not accept event <id>` |
| MateMail | `event <id> not queued (<type>); the sweep will retry` · `event <id> dispatched to <n> device(s)` · `PostBox push to device <uuid> via <fcm|wns>: <status> (<code>)` · `device <uuid> registered|refreshed for mailbox <pk>` · `FCM credentials are unusable: <type>` · `WNS authentication failed; check the Entra ID settings` · sweep and prune counts |

No line anywhere contains a token, a channel URI, a secret, message content
or, on the MateMail side, a mailbox address. Mailboxes are logged by primary
key.

---

## 10. Privacy

- **Stored:** the event's mailbox, folder name, UIDVALIDITY and UID for up to
  7 days; the registration's platform, provider, token and diagnostics until
  deleted or revoked.
- **Never stored or sent:** message body, HTML, headers, subject, sender,
  recipients, attachment names, sizes or raw RFC 822. No Django message rows.
  Dovecot remains the only mail store.
- **Never in a push:** the mailbox address, sender, subject, preview,
  recipients, attachment names, session cookie or any token. Provider
  infrastructure is someone else's network. Microsoft's own guidance is that
  notifications "should never include confidential, sensitive, or personal
  data".
- **The token** is stored in the clear, because the provider needs it verbatim
  and no custom encryption is invented. The residual exposure is the database
  itself. It is never returned by the API, logged, placed in an error, or
  included in a payload.

---

## 11. Known limitations

1. **The relay queue is in memory.** Events queued in the API when it restarts
   are lost: the push is missed and the mail is unaffected. The app's sync on
   launch covers it.
2. **Only LMTP deliveries produce events.** An IMAP APPEND (a client's Sent
   copy, a draft), a move between folders or a `doveadm` import produces
   none, by design.
3. **Events older than 30 minutes are not sent.** For example, if Celery is
   down that long, those pushes are dropped rather than sent late.
4. **At-least-once, not exactly-once.** Duplicates are possible; `event_id`
   exists so the app can drop them.
5. **Not proven against live providers.** Nothing has been sent through FCM or
   WNS; both adapters are verified against the providers' documentation and
   exercised against mocked HTTP.
6. **Sieve is not active** on the Native Engine, so `folder` is always
   `INBOX` today (see §3).
7. **WNS needs Microsoft's PFN → AppId mapping** before a packaged app can
   receive anything, and that is a manual request.
8. **Not deployed:** new API and Dovecot images must be built and rolled out
   before any of the engine half runs.

---

## 12. PostBox-App client contract (exact)

This is the contract the PostBox Flutter app implements in the next task.
Everything here is what the server does today; nothing is aspirational.

### 12.1 Authentication

Every device call is an ordinary PostBox API call, authenticated by the
`__Host-postbox_session` cookie from `POST /api/postbox/auth/login/`, exactly
like `/api/postbox/folders/`. There is no separate push credential.

- A call without a valid session answers **403**
  (`{"detail": "..."}`); DRF sends no challenge, so it is 403, not 401.
  The response means "sign in again".
- Each signed-in account registers with **its own** session cookie.

### 12.2 `installation_id`

- A random **UUID v4**, generated by the app **once per installation** and kept
  in app-local storage.
- Never derived from a hardware ID, an advertising ID, the push token or the
  account.
- **Stable** across launches, sign-outs, sign-ins and token refreshes.
- **New** after a reinstall or when app data is cleared, because that is a
  new installation.
- The **same value for every account** signed in on this installation. The
  server keys a registration on `(mailbox, installation_id, provider)`, so
  each account gets its own row.
- If the same provider token arrives for the same mailbox under a different
  `installation_id`, the server keeps only the newest, so a reinstall that
  keeps its FCM token does not double-register.

### 12.3 Register or refresh: `POST /api/postbox/devices/`

Request (`Content-Type: application/json`):

```json
{
  "installation_id": "3f0c8f7e-6a2b-4c1d-9e2f-5b7a1c3d4e5f",
  "platform": "android",
  "provider": "fcm",
  "token_type": "registration_token",
  "token": "<provider token>"
}
```

| Field | Required | Values |
|---|---|---|
| `installation_id` | yes | UUID (§12.2) |
| `platform` | yes | `android` or `windows` |
| `provider` | yes | `fcm` with `android`, `wns` with `windows`. Any other pair → 400. |
| `token_type` | no | FCM: `registration_token` (default) or `fid`. WNS: `channel_uri` (default, and the only one). |
| `token` | yes | FCM: 1–4096 characters of `A-Z a-z 0-9 _ - : .`. WNS: the channel URI, `https`, host `notify.windows.com` or a subdomain of it, no userinfo, port 443, at most 2048 characters. Not trimmed: whitespace → 400. |

- Android: `token` is `FirebaseMessaging.getToken()` with `token_type`
  `registration_token`. Send `fid` only if the app deliberately targets
  Firebase Installation IDs.
- Windows: `token` is `PushNotificationChannel.Uri` from the Windows App SDK's
  `CreateChannelAsync`, with `token_type` `channel_uri`.
- Any other field is ignored; send none.

Responses:

| Status | Meaning | Body |
|---|---|---|
| **201** | new registration | device object |
| **200** | the existing `(mailbox, installation_id, provider)` row updated: new token, re-enabled, moved to this session | device object (same `id` as before) |
| **400** | invalid request; do not retry unchanged | DRF field errors, e.g. `{"token": ["..."]}` |
| **403** | no valid session | `{"detail": "..."}` |
| **409** | this mailbox already has 20 registrations | `{"detail": "This mailbox has too many devices registered for notifications."}` |

Device object (the token is **never** returned):

```json
{
  "id": "9b1d6c1e-2f3a-4b5c-8d7e-0f1a2b3c4d5e",
  "platform": "android",
  "provider": "fcm",
  "token_type": "registration_token",
  "enabled": true,
  "created_at": "2026-09-26T10:00:00.000000+00:00",
  "updated_at": "2026-09-26T10:00:00.000000+00:00",
  "last_seen_at": "2026-09-26T10:00:00.000000+00:00"
}
```

**Store `id` per account.** It is the `device_registration_id` every push for
that account carries. Always replace the stored value with the one from the
latest response.

### 12.4 List: `GET /api/postbox/devices/`

**200** `{"results": [<device object>, ...]}`, holding this mailbox's
registrations from every installation, most recently seen first, never with a
token. **403** without a session. Use it to see whether this installation's
registration still exists and is `enabled`.

### 12.5 Remove: `DELETE /api/postbox/devices/<id>/`

**204** (no body) when removed. **404** `{"detail": "Not found."}` when it is
not this mailbox's or is already gone; treat that as success. **403** without
a session.

### 12.6 When to call what

| Situation | Client action |
|---|---|
| Sign-in completed, push allowed on this device | `POST` register |
| Every app launch, while signed in | `POST` again (idempotent, 200). Keeps `last_seen_at` fresh and picks up a changed token. |
| FCM token refresh (`onNewToken`) | `POST` with the new token, same `installation_id` |
| Windows launch | request a channel (WNS channels expire after 30 days, so request one at every launch) and `POST` it |
| User turns notifications off for an account | `DELETE` that account's registration |
| User turns them back on | `POST` |
| Registration shows `enabled: false` | the provider rejected the token. Obtain a **new** provider token (FCM `deleteToken` + `getToken`, or a new WNS channel), then `POST`. Re-posting the dead token re-enables it only until the next push fails. |
| `POST` answers 409 | too many registrations. Surface it, or remove this mailbox's stale ones via the list, then retry. |
| Sign-out (`POST /api/postbox/auth/logout/`) | nothing else required: the server deletes that session's registrations. Also forget the stored `id` locally. |
| Any call answers 403 (session revoked or expired, "sign out everywhere", password changed elsewhere) | sign in again, then `POST` register. Revocation already deleted the old row; an expired session's row is updated in place. |

### 12.7 Session and revocation semantics

- A registration receives push only while its session is active (not revoked,
  not expired) and the mailbox may sign in. Both are checked at dispatch and at
  send.
- Revocation — sign-out, sign-out-everywhere, a password change on another
  session, or any server-side revoke — **deletes** the affected
  registrations immediately.
- Expiry (12 hours, or 30 days with "remember me") stops push immediately.
  The row stays, unselected, until the session is pruned a week later. A
  re-registration from a new session within that time updates it (200, same
  `id`); after it, a new one is created (201, new `id`).
- A suspended mailbox or organization receives no push. Its next API call is
  refused and its session revoked.

### 12.8 The push payload

**Android (FCM): a data-only message** (no `notification` block), high
priority, TTL 1 hour. Every value is a string:

```json
{
  "version": "1",
  "kind": "new_mail",
  "event_id": "2b8f1a6e-5d3c-5b7a-9e1f-4c6d8a0b2e3f",
  "device_registration_id": "9b1d6c1e-2f3a-4b5c-8d7e-0f1a2b3c4d5e",
  "folder": "INBOX",
  "uid_validity": "1790366690",
  "uid": "7"
}
```

**Windows (WNS): a raw notification.** The payload bytes are the same map as
compact UTF-8 JSON (`X-WNS-Type: wns/raw`, cached for an offline device, TTL
1 hour). Do not depend on key order.

| Key | Always | Meaning |
|---|---|---|
| `version` | yes | `"1"`. Any other value: do a normal sync of that account's INBOX and nothing else. |
| `kind` | yes | `"new_mail"`, the only kind today. Ignore any other kind. |
| `event_id` | yes | UUID of **one saved message**, derived from its mailbox, folder, UIDVALIDITY and UID. The same message always has the same id, and the **same id can arrive more than once** (provider redelivery, retries). Drop repeats: keep recently seen ids for at least 24 hours. |
| `device_registration_id` | yes | the `id` from §12.3. It says which account this is. Unknown (for example, signed out locally): drop the push. |
| `folder` | yes | the folder Dovecot saved the message to, as Dovecot names it (UTF-8, `/` separator). Today always `INBOX`. Match it against the folders `GET /api/postbox/folders/` returns; if nothing matches, sync INBOX. |
| `uid_validity`, `uid` | together or not at all | decimal strings of the IMAP UIDVALIDITY and UID the commit assigned. **Present** (as they always are from the current engine): the app may fetch exactly that message with `GET /api/postbox/messages/<folder>/<uid>/?uid_validity=<uid_validity>`. The server refuses a stale UIDVALIDITY with 502 `{"detail": "This folder has changed. Please refresh and try again."}`, the same status as any other mail-access failure. So on **any** failure, resync with `GET /api/postbox/messages/?folder=<folder>`, whose response carries the folder's current `uid_validity`. **Absent** (reserved for an event that names no message): sync the folder, or INBOX. |

**Never in a payload:** the mailbox address, sender, subject, preview or body,
recipients, attachment names, message size or flags, the session cookie or any
token.

### 12.9 Handling a push (required behaviour)

1. **Treat it as a hint, not a fact.** A push is not authenticated to the app;
   it only prompts an authenticated fetch. Validate formats (UUIDs, decimal
   UIDs, a folder of at most 255 characters) and never act on them beyond
   fetching.
2. Map `device_registration_id` to the account, drop duplicates by
   `event_id`, then fetch through the PostBox API with that account's session.
3. Decide locally whether to notify, using the device's notification settings
   and the fetched message (for example, skip one already marked read). What
   a notification displays comes from the authenticated fetch and the user's
   preview setting, never from the push. Reading a message through the API
   does not mark it read.
4. Push is best-effort: keep syncing on launch and resume exactly as today.
   A missing push must never mean missing mail in the app.
