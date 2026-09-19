# SECURITY.md — Security Architecture and Controls

**Product:** MateMail
**Last updated:** 2026-09-11
**Classification:** Engineering reference

---

> ## ⚠️ Read this before trusting any table below
>
> This document mixes **verified controls** with **aspirational design that was
> never built**. Treat an unmarked row as a claim to verify, not a fact.
>
> **Verified and pinned by tests** (updated 2026-09-11):
> Role-Based Access Control → *Enforcement*, *Two-factor challenge tokens* and
> *Internal endpoints*; Rate Limiting; Login and Second-Factor Protection;
> Workspace and Plan Limits; API Key Scopes; Token Storage;
> Content-Security-Policy; DKIM Private Key Storage; Domain Ownership
> Verification; and the Web Login table.
>
> **Corrected in P3** — these claims were false and are no longer made:
> - "Password hashing — Argon2 via `django-argon2`". The project used PBKDF2.
>   It now genuinely uses Argon2id via `argon2-cffi`, with PBKDF2 retained so
>   existing accounts keep working and are upgraded on next login.
> - "Session invalidation — Redis-backed token revocation list". It is
>   simplejwt's database-backed blacklist, and the table now says so.
> - "Suspicious login alerts" and "Session listing" — marked as not built.
> - "2FA — `django-otp` or `pyotp`" — it is `pyotp`, with a custom flow.
> - The IMAP/SMTP Auth table described **Stalwart**, which was never used, and
>   is now marked as target design for an engine that is not installed.
>
> **Still to verify:** the Anti-Relay and Mail Engine Layer tables describe an
> engine that does not exist yet. They are target design, not current
> behaviour, and nothing in them is in force. Treat them as a specification to
> implement in P4, not as a description of today.

---

## Related documents

| Document | Covers |
|---|---|
| `docs/MAIL_POLICY.md` | Sending policy, the approval gate, abuse response, deliverability, and the operator runbook (P5) |
| `docs/MAIL_ENGINE.md` | The adapter boundary, DKIM lifecycle, engine runtime |
| `docs/DECISIONS.md` | DEC-017 (policy framework), DEC-018 (free accounts, design) |

---

## Principles

1. **No open relay** — SMTP submission requires authentication, always.
2. **Tenant isolation** — A tenant can never read, write, or affect another tenant's data.
3. **Least privilege** — Every API key, service account, and role has the minimum needed scope.
4. **Defense in depth** — Controls at network, application, and data layers.
5. **Fail closed** — Unknown/invalid state defaults to deny, not allow.
6. **No secret logging** — Passwords, tokens, and private keys are never written to logs.

---

## Anti-Relay Controls

> **Corrected 2026-09-12.** This section previously described Stalwart, which
> was option A in DEC-001 and was **not** chosen. The engine is
> Postfix / Dovecot / Rspamd orchestrated by mailcow. The tables below describe
> what is actually implemented.

MateMail's decisions are made by the SMTP policy bridge
(`apps/smtp_policy/views.py`), which the engine consults over an
internal-secret-authenticated HTTP endpoint. It answers `OK`, `REJECT`
(permanent, 5xx) or `DEFER` (temporary, 4xx). Full policy: `docs/MAIL_POLICY.md`.

### SMTP Submission (Port 587)

| Rule | Implementation | State |
|------|---------------|-------|
| Authentication required | Dovecot SASL; the engine accepts no anonymous submission | OK |
| Sender must equal the authenticated account | first check in `OutboundPolicyView`, before anything else | OK (P5) |
| Only hosted mailboxes may send | sender must resolve to a `Mailbox` MateMail holds | OK (P5) |
| Unapproved workspace rejected | `assert_can_send_mail` — requires `approved_at` | OK (P5) |
| Suspended workspace rejected | `assert_can_send_mail`, **plus** engine-side domain deactivation | OK (P5) |
| Outbound disabled rejected | `Tenant.outbound_disabled`, the lighter abuse response | OK (P5) |
| Suspended or disabled mailbox rejected | checked before workspace policy | OK |
| Inactive sending domain rejected | `Domain.status` | OK |
| Per-mailbox hourly send limit | plan-driven, atomic Redis script; **defers**, never rejects | OK (P5) |
| Per-workspace daily send limit | same script, same call | OK (P5) |
| Engine-side per-mailbox limit | `set_mailbox_rate_limit` — independent of the above | OK (P4C-A) |
| Platform sender allowed, not exempted | own allowance, own finite ceiling, same identity rule | OK (P5) |
| **Policy consulted at submission time** | `check_policy_service` before `permit_sasl_authenticated` (`deploy/engine/postfix-extra.cf`) | built, tested, **not yet deployed** |

### SMTP Inbound (Port 25)

| Rule | Implementation | State |
|------|---------------|-------|
| Only accept mail for hosted domains | `Domain` lookup in `InboundPolicyView` | OK |
| Unknown domain permanently rejected | the answer will not change | OK |
| Unknown recipient permanently rejected | deferring would make MateMail a backscatter source | OK |
| Known alias accepted | alias-aware since P5; previously every alias bounced | OK (P5) |
| Suspended workspace/mailbox **deferred** | reversible, so the mail must survive it | OK (P5) |
| Outbound-disabled workspace still receives | cutting inbound punishes the sender, not the abuser | OK (P5) |
| SPF / DKIM / DMARC verification | Rspamd | OK |
| Spam scanning | Rspamd + ClamAV | OK |
| Rate limit per source IP | Postfix + netfilter-mailcow | OK |

---

## Authentication Security

### Web Login

| Control | Implementation | State |
|---------|---------------|-------|
| Password hashing | Argon2id (`argon2-cffi`), with PBKDF2 retained for existing accounts | ✅ P3 follow-up |
| Password reset tokens | Hashed before storage, single-use, 1-hour TTL | ✅ |
| JWT tokens | Short-lived access (15 min) + refresh (7 days) | ✅ |
| Refresh token storage | HttpOnly + Secure + SameSite=Strict cookie, scoped to `/api/auth/` | ✅ P3c |
| Session invalidation | simplejwt's **database-backed** blacklist (not Redis); a password reset revokes every refresh token | ✅ |
| 2FA (TOTP) | `pyotp`, RFC 6238, `valid_window=1` | ✅ |
| Backup codes | Hashed before storage, one-time use | ✅ |
| 2FA challenge token | Opaque, server-side, 5-min TTL, single-use, password-bound | ✅ Phase 0 |
| Login rate limit | 5 failures / 15 min per IP **and** per account | ✅ P3b |
| 2FA brute-force limit | 5 failures / 15 min per user, across challenges | ✅ P3b |
| TOTP replay | An accepted code is single-use for its validity window | ✅ P3b |
| Suspicious login alerts | New IP/country → email alert | ❌ Not built |
| Session listing | Users can see and revoke active sessions | ❌ Not built |

One correction to earlier drafts of this table: the 2FA implementation is
`pyotp` — `django-otp` was never used.

Password hashing was PBKDF2 while these drafts claimed Argon2. That gap was
found during P3b and recorded honestly rather than quietly fixed; it is now
closed for real:

```python
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",   # preferred
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]
```

New and changed passwords use Argon2id. **The older hashers are retained on
purpose.** A password hash cannot be converted to another algorithm without the
password, so removing PBKDF2 would not migrate existing accounts — it would
lock them out. Instead Django re-hashes an account on its next successful
login, which is the one moment it holds the plaintext. No data migration is
involved, and none could be.

Parameters are argon2-cffi's defaults, which Django tracks. They are not tuned
here: a memory or time cost chosen without measuring this server is not better
than the maintained default, and a wrong one is worse.

Covered by `tests/test_password_hashing.py`: a new password is Argon2, a
legacy PBKDF2 hash still authenticates, a successful login upgrades it in
place, a wrong password still fails and does *not* trigger an upgrade, and the
upgrade happens through the real `/api/auth/login/` path rather than only in
ORM calls.

### IMAP/SMTP Auth

**Status: the Mail Engine is installed and live as of P4B.** Dovecot performs
SASL authentication for submission and IMAP, and the controls below are the
engine's own — MateMail does not implement them and does not hold mailbox
passwords. No public mail port is open, so none of this is currently reachable
from the Internet. "Stalwart" in earlier drafts predates DEC-001, which selected
mailcow (Postfix / Dovecot / Rspamd).

| Control | Target implementation |
|---------|----------------------|
| Password hashing | Mail Engine: Argon2 or bcrypt |
| Brute-force protection | Mail Engine: block IP after N failed attempts |
| TLS required | All auth over TLS (993, 587, 465) — no plaintext auth |
| Disabled mailbox | Mail Engine: auth rejected for disabled principals |
| Suspended tenant | Mail Engine: auth rejected for all tenant mailboxes |

---

## Tenant Isolation

### Data Layer

Every customer-owned table has a `tenant_id` foreign key. Queryset-level isolation is enforced via:

```python
class TenantScopedManager(models.Manager):
    def get_queryset(self):
        # Automatically filtered by request.tenant in middleware
        ...
```

**No query touches tenant-owned data without an explicit `.filter(tenant=request.tenant)`.** This is enforced in code review and tests.

### API Layer

Every API endpoint that touches tenant data:
1. Checks `request.user.is_authenticated`
2. Resolves `request.tenant` from JWT claim or session
3. Verifies the resource belongs to `request.tenant`
4. Enforces role permissions for the action

### Mail Engine Layer

Domains in the Mail Engine are scoped per-domain: a mailbox belongs to exactly
one domain, and a verified domain belongs to exactly one tenant — enforced by a
partial unique index in MateMail's own database, not by the engine (P3a).

**The engine itself has no tenant concept**, which is why that mapping is
MateMail's responsibility and why engine-wide reads (`get_queue_status`,
`get_quarantine_items`) are documented at the port as requiring tenant mapping
before anything is stored or displayed. Treating an engine-wide list as
tenant-scoped is the shape a cross-tenant leak would take here.

There is no API path that allows Tenant A to read or affect Tenant B's mail.

---

## Role-Based Access Control

| Role | Capabilities |
|------|-------------|
| Owner | All capabilities including billing and team management |
| Admin | Domains, mailboxes, aliases, forwarding, DNS, spam, queue, logs, backups, settings |
| Support | View domains, mailboxes, logs, queue; limited actions |
| Read-only | View only; no mutations |
| Platform Admin | Internal staff; cross-tenant management; separate permission check |

Platform admin access is controlled by `User.is_platform_admin` (separate from tenant roles) and enforced by a dedicated `IsPlatformAdmin` DRF permission class on all `/api/platform/` endpoints.

### Enforcement (implemented in Phase 0)

The table above is enforced by permission classes in `apps/tenants/permissions.py`, not by convention:

| Class | Reads | Writes | Used on |
|-------|-------|--------|---------|
| `HasTenantAccess` | any active member | **not role-aware — read-only views only** | billing, DNS record listing |
| `TenantReadAdminWrite` | any active member | owner, admin | domains, mailboxes, aliases, forwarding |
| `TenantReadSupportWrite` | any active member | owner, admin, support | domain DNS re-check only |
| `IsTenantAdmin` | owner, admin | owner, admin | logs, queue, quarantine, backups, invites, API keys, mailbox status/reprovision |
| `IsEmailVerified` | any | verified accounts only | domain + mailbox provisioning |

`HasTenantAccess` checks only that *some* active membership exists. Before Phase 0 it guarded ten mutating endpoints, so a `read_only` member could delete a domain or create a forwarding rule to an external address. Never use it on a view that mutates tenant state.

`TenantReadSupportWrite` is the one deliberate exception to admin-only writes: re-running a domain's DNS check is non-destructive and diagnostic. Do not widen it without recording the reason here — `read_only` must still receive 403.

The matrix is pinned by `tests/test_role_permissions.py`, which drives every mutating endpoint as each of the four roles.

### Two-factor challenge tokens

A 2FA challenge (`partial_token`) is **not** a JWT and must never become one. It is
32 bytes of opaque entropy; the user/tenant binding lives server-side in the
Redis-backed cache keyed by the token's SHA-256, with a 5-minute TTL, single-use
semantics, and destruction after 5 failed code attempts. It is also bound to the
password hash that created it, so a password change voids it immediately.

Before Phase 0 this was a real `AccessToken`, which DRF's `JWTAuthentication`
accepted — a password alone granted full API access with 2FA never presented.
See `apps/accounts/challenge.py` and `tests/test_two_factor.py`.

### Internal endpoints

Every endpoint that authenticates with `INTERNAL_API_SECRET` rather than a user
credential **must** be routed under `/api/internal/`, because that prefix is what
the edge nginx config denies. Comparison uses `hmac.compare_digest`, and an
unset secret fails closed. Pinned by `tests/test_internal_endpoints.py`.

---

## Rate Limiting

**Status: IMPLEMENTED (P3b).** The limits below are enforced and covered by
tests that drive each endpoint until it refuses — not by asserting that a
setting exists.

### The identity every per-IP limit depends on

`X-Forwarded-For` is written by the caller *and* by everything upstream of
them, so the header as a whole is attacker controlled. DRF's own
`BaseThrottle.get_ident` joins the entire header and uses it as the throttle
key when `NUM_PROXIES` is unset — which it was — so varying the header per
request produced a fresh bucket every time and every per-IP limit was
decorative.

`apps.security.client_ip.get_client_ip` resolves the address instead:

- `TRUSTED_PROXY_COUNT` (default `0`, **`1` in production**) states how many
  proxy hops in front of the application may be trusted.
- Host-native nginx sets `X-Forwarded-For $proxy_add_x_forwarded_for`, which
  appends the address nginx actually saw. With one hop the *rightmost* entry is
  the only one our own infrastructure wrote; everything left of it is a claim.
- A value that does not parse as an IP address is discarded rather than used as
  a cache key, and an unresolvable caller shares one bucket rather than getting
  a private one.

The same function now supplies the `ip_address` on audit records. Those
previously took the leftmost `X-Forwarded-For` entry, so an audit trail could
record an attacker-chosen address — worse than recording none, because it reads
as evidence.

### Limits

Defined once in `apps.security.limits` so the documentation, the views and the
tests cannot drift apart.

| Endpoint / Operation | Limit | Keyed on |
|---------------------|-------|----------|
| POST /api/auth/login/ | 5 failures / 15 min | client IP |
| POST /api/auth/login/ | 5 failures / 15 min | account |
| POST /api/auth/signup/ | 3 / hour | client IP |
| POST /api/auth/forgot-password/ | 3 / hour | normalised email |
| POST /api/domains/:id/verify-ownership/ | 10 / hour | domain |
| POST /api/domains/:id/check/ | 10 / hour | domain (shared budget) |
| POST /api/mailboxes/ | 20 / hour | tenant |
| POST /api/auth/2fa/verify/ | 5 failures / 15 min | user (across challenges) |
| POST /api/auth/2fa/setup/, /2fa/disable/ | 10 failures / 15 min | user |
| DNS background checks | 1 / 5 min | domain (Celery, P3a) |
| Authenticated API (general) | 60 / min | user |
| Unauthenticated API (general) | 60 / min | client IP |
| Unauthenticated auth endpoints | 5 / min | client IP |
| Authenticated auth actions | 10 / min | user |
| SMTP AUTH attempts | 5 / min per IP | *Mail Engine — not yet installed* |
| Outbound SMTP send | 100 / hour per mailbox | *Mail Engine — not yet installed* |
| Outbound SMTP send | 1000 / day per tenant | *Mail Engine — not yet installed* |

Login limits count **failures**, and a correct password clears both counters.
Counting every attempt would let one person on a shared NAT gateway lock out an
entire office, while doing nothing extra against brute force — failures are
what an attack produces.

Refusals are HTTP **429** with a `Retry-After` header, raised through DRF's
`Throttled` so the shape is identical everywhere.

### What is deliberately not limited

`/api/health/` and `/api/internal/` are exempt. `/api/internal/` is the Mail
Engine policy bridge, which Postfix will consult per message; a 60/min cap
there is a mail outage, not an abuse control. It is denied at the edge by nginx
and authenticated with `INTERNAL_API_SECRET`. Celery tasks do not pass through
DRF at all.

### Implementation note

`django-ratelimit` was evaluated and not adopted. Its `Ratelimited` exception
maps to 403 rather than 429, and its IP keying carries the same untrusted-header
problem that had to be solved here regardless — so it would have been a
dependency wrapped in enough custom code to be the thing itself. The limits are
DRF-native throttle classes over one shared cache primitive
(`apps.security.ratelimit`), using atomic `cache.add`/`cache.incr` on
wall-clock-aligned fixed windows.

---

## Login and Second-Factor Protection

**Status: IMPLEMENTED (P3b).**

Phase 0 built the challenge token: opaque (not a JWT, so it cannot be replayed
as an API credential), server-side in Redis, five-minute TTL, single-use via an
atomic delete, bound to the account's password hash so a reset voids it, and
destroyed after five failed attempts. That design is unchanged.

P3b adds the protections that a per-challenge counter cannot provide:

- **Per-account and per-IP login lockout**, as above.
- **A per-user second-factor budget.** The Phase 0 counter caps attempts
  against *one* token. An attacker who holds the password simply logs in again
  for a fresh challenge after every fifth failure — unbounded guesses against
  six digits. The P3b limit follows the user across every challenge they hold.
- **TOTP replay prevention.** `pyotp` is called with `valid_window=1`, so a code
  is accepted for the timestep before, during and after its own — ninety
  seconds. Anyone who observes one (a phishing relay, a shared screen, a
  shoulder surf) can present it again within that window against a challenge of
  their own. An accepted code is now claimed for that user until it expires,
  via an atomic `cache.add`, so it works exactly once.
- **Limits on enrolment and disable.** Both were decorated with an
  `AnonRateThrottle` subclass, which returns immediately for an authenticated
  request — so both were entirely unlimited. A signed-in session could grind
  six digits against a known enrolment secret, or guess the account password to
  switch the second factor off.

Every refused sign-in returns the same message and status. "Wrong password",
"no such account", "account disabled" and "temporarily locked" are each an
oracle if they can be told apart. The password-reset endpoint answers
identically whether or not the address is registered, and its rate limit counts
requests for unregistered addresses too — a limit that applied only to real
accounts would answer 429 for those and 200 forever for the rest, reintroducing
the oracle the uniform response exists to prevent.

---

## Workspace and Plan Limits

**Status: IMPLEMENTED (P3b).**

`MAX_WORKSPACES_PER_USER` (default **5**, overridable by environment variable)
caps how many workspaces one user may **own**. Each workspace carries its own
trial subscription, domains and mailboxes, so an uncapped endpoint is both a
trial-abuse path and a way for one account to consume provisioning capacity.
Workspaces the user has merely been *invited* into do not count — that is not
abuse, and counting it would stop legitimate users creating their own.

`Plan.max_members` is now genuinely enforced. `check_member_limit` existed in
`apps.billing.utils` before P3b and **was never called from anywhere**: the
number was in the database and nothing consulted it. It is now checked on every
path that can take a seat:

| Path | Behaviour at the limit |
|------|------------------------|
| `POST /api/workspaces/:id/members/` | 403, no membership row created |
| Reactivating a removed member | 403 — reactivation is a seat like any other |
| `POST /api/teams/invites/` | 403, no invite created |
| `POST /api/teams/invites/accept/` | 403 if the plan was downgraded meanwhile |

**Pending invitations reserve a seat.** This is the deliberate choice. Counting
only accepted members lets a three-seat workspace send thirty invitations —
each acceptance individually looks fine against a count taken before any of the
others landed — and end up with thirty members. Reserving means an admin
revokes an invitation to free a seat, which is behaviour a customer can reason
about. Expired invitations release their seat automatically.

Each check runs inside the transaction that takes the seat, with the tenant row
locked (`select_for_update`). Without the lock, two admins inviting at the same
moment both read the pre-change count and both succeed. Workspace creation
locks the user row instead, because the row being counted does not exist yet.

---

## API Key Scopes

**Status: IMPLEMENTED (P3b).**

Two defects were found and fixed here, and the second is the more serious.

**API keys inherited their creator's identity.** `APIKeyAuthentication` returned
`api_key.created_by`, so a key acted as that user with every permission they
held. A key minted by a workspace owner could do anything the owner could, and
a key minted by a member of MateMail staff was a platform-admin credential
sitting in a customer's configuration file.

**API keys had never authenticated at all.** `JWTAuthentication` was listed
first in `DEFAULT_AUTHENTICATION_CLASSES`, and simplejwt claims every
`Bearer ...` header and *raises* `InvalidToken` when the value will not parse —
it does not return `None` and defer to the next authenticator. Every `mm_` key
was therefore rejected with 401 before `APIKeyAuthentication` ran.
`TenantMiddleware` had the same bug for the same reason, so no tenant was
resolved either. Both now dispatch on the credential type rather than
discovering it from an exception.

### The scope model

Five values, resource shaped. A long list of fine-grained scopes reads as
thorough and in practice gets granted wholesale, because nobody can reason
about it.

| Scope | Grants |
|-------|--------|
| `read` | Every safe method within the key's own workspace |
| `domains:write` | Domains, DNS checks, ownership verification |
| `mailboxes:write` | Mailboxes and their credentials |
| `routing:write` | Aliases and forwarding |
| `admin` | Workspace administration — team, keys, billing, backups, queue, quarantine |

Every key holds `read`, and **nothing else is implied by who created it**: a key
minted by an owner is read-only until a write scope is named explicitly. Scopes
that are missing, empty or unrecognised degrade to read-only rather than to
unrestricted or unusable.

### Enforcement

`APIKeyScopeMiddleware`, not a DRF permission class. DRF *replaces*
`DEFAULT_PERMISSION_CLASSES` for any view that declares its own
`permission_classes` — which is nearly every view here — so a global default
would enforce nothing. Middleware sees every request regardless, and cannot be
switched off by forgetting to add a class to a list.

It is **default-deny**: a mutating path with no scope mapped is refused, so an
endpoint added later is closed to API keys until somebody maps it deliberately.

Three prefixes no scope can ever reach:

- `/api/platform/` — internal staff surface, cross-tenant by design.
  `IsPlatformAdmin` refuses API-key requests as well, so the rule survives an
  endpoint being moved out of that prefix.
- `/api/internal/` — the Mail Engine and webmail bridge.
- `/api/auth/` — a key is not a session and must not mint one, change a
  password, or alter a second factor.

Key creation, revocation and scope changes are written to the audit log
(`api_key_created`, `api_key_revoked`, `api_key_scopes_changed`). The secret is
shown once at creation and never stored. Tenant isolation is unchanged: a key
reaches only its own workspace.

### Migration note

`teams.0002_apikey_scopes` gives every existing key `["read"]`. Any existing
integration that writes through an API key will start receiving 403 and needs
its scopes granted deliberately. That is the intended direction — the
alternative is grandfathering in exactly the privilege the change exists to
remove. (In practice no key has ever worked, per the authentication defect
above.)

---

## Token Storage

**Status: IMPLEMENTED (P3c).**

The refresh token used to be returned in the JSON body and kept in
`localStorage`. It is the durable credential — seven days, and it mints access
tokens for all of them — so one XSS bought a week of silent access, revocable
only by the victim logging out in that browser.

It is now a cookie the browser will not hand to JavaScript:

| Attribute | Value | Why |
|-----------|-------|-----|
| `HttpOnly` | yes | The entire point: `document.cookie` cannot see it |
| `Secure` | yes in production | Never crosses a plaintext connection |
| `SameSite` | `Strict` | No cross-site request of any kind carries it |
| `Path` | `/api/auth/` | Every other API call carries no browser-attached credential |
| `Max-Age` | the token's own lifetime | The browser drops it exactly when the server would reject it |

`SameSite=Strict` rather than `Lax` because the usual reason to prefer Lax —
keeping a session across a link from another site — does not apply: the refresh
happens by same-origin XHR after the page has loaded, and same-origin requests
always carry Strict cookies.

### CSRF

Cookie authentication normally trades XSS exposure for CSRF exposure. That
trade is avoided here rather than accepted:

- **The API is not cookie-authenticated.** Every endpoint still requires a
  bearer access token in an `Authorization` header, which a cross-site request
  cannot set. The cookie authenticates exactly one operation — the token
  exchange at `/api/auth/refresh/`.
- `SameSite=Strict` already prevents any cross-site request from carrying it.
- A forged refresh would return its result into a response the attacker's
  origin cannot read.

### Residual exposure, stated plainly

The **access token is still readable by JavaScript** (`sessionStorage`). An XSS
can act as the user until it expires — 15 minutes by default — and cannot
extend that window, because it cannot reach the refresh token. Removing that
last exposure means holding the access token in memory only, at the cost of a
full re-authentication on every page load. It is not claimed to be solved.

There is exactly **one** mechanism: the refresh endpoint reads the cookie and
nothing else. A token supplied in the request body is rejected — two mechanisms
would mean the weaker one defines the security of the pair. Logout and password
reset both clear the cookie at its own path.

Browsers that used a pre-P3c build still hold a live refresh token in
`localStorage`; the app deletes it on first load of the new build.

---

## Content-Security-Policy

**Status: IMPLEMENTED (P3c).** Owned by the application, not by nginx.

P2.5 shipped `script-src 'self' 'unsafe-inline'`, which removes CSP's main
protection against injected script. It was not a design choice: the App Router
emits inline `<script>` tags carrying the hydration payload, and without them
React never hydrates and every page renders as a dead static shell.

`frontend/middleware.ts` now issues a fresh nonce per request and Next.js
stamps it onto every script tag it emits. The production policy is:

```
default-src 'self';
script-src 'self' 'nonce-<per-request>' 'strict-dynamic';
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:;
font-src 'self' data:;
connect-src 'self';
frame-ancestors 'self'; base-uri 'self'; form-action 'self'; object-src 'none';
upgrade-insecure-requests
```

`'unsafe-eval'` is added in development only, gated on `NODE_ENV`.
`style-src` keeps `'unsafe-inline'`: Next injects critical CSS inline, there is
no style-nonce equivalent that survives streaming, and injected CSS cannot
execute.

**nginx no longer sets CSP for frontend responses.** It cannot know the nonce,
and a second CSP header is not additive — a browser enforces the intersection
of every policy it receives, so leaving one there would silently constrain the
app under a policy nobody maintains. nginx still sets CSP for the responses it
proxies from Django: `default-src 'none'` for `/api/` and `/static/`, and a
policy retaining `'unsafe-inline'` for `/django-admin/`, which ships inline
scripts and has no nonce mechanism.

### Every route renders per request

Next.js can only apply a per-request nonce to a route it renders at request
time. A statically prerendered page has its HTML — and its inline hydration
scripts — fixed at build time with no nonce, and serving that under a nonce
policy blocks the scripts: the P2.5 failure, with the protection now actively
working against us. `app/layout.tsx` therefore sets `dynamic = "force-dynamic"`.

This was **measured, not assumed**. Before that change the production server
emitted 19 script tags and 0 nonce attributes.

### Verification

`frontend/scripts/csp-browser-check.js` drives a real Chromium against a
production build and asserts the header shape, that the client runtime booted,
that React attached, that the login form updates state, that client-side
navigation works, and that no CSP violation appears in the console. 17/17
passed for P3c.

It is a manual tool, not CI — it needs a local Chrome and `puppeteer-core`.
Run it after any change to the middleware, the nginx template or the Next.js
version, and against the deployed site after a release. curl cannot substitute:
every HTML document and JS chunk returns 200 whether or not the page works.

---

## Transactional Email

**Status: CODE COMPLETE (P3c). Real delivery NOT verified — DEFERRED TO P4 per
DEC-013.** Do not read anything below as evidence that mail has been delivered;
no transactional message has ever left this platform.

Account verification, password resets, team invitations and security notices are
delivered by **MateMail's own Mail Engine**, not a third-party SMTP provider.
DEC-013 settled this: MateMail owns its mail infrastructure end to end, and
running the platform's own mail through a vendor contradicts that.

P3c originally routed these through an external provider, on the reasoning that
a platform whose password reset stops working when its mail system is down is a
platform nobody can recover an account on. **That risk is accepted rather than
solved**, and P4 owns mitigating it — at minimum, alerting that distinguishes
"platform mail is failing" from "customer mail is failing", so the outage is
known before customers report it.

The code needs no change for this: it is plain `django.core.mail` SMTP with no
provider-specific coupling, so pointing it at the Mail Engine is configuration.
Note that the `EMAIL_HOST` guard rejects loopback, so the engine must be reached
by a named host.

Two defects were fixed:

- **Every send used `fail_silently=True`.** Django swallows the exception and
  returns 0, so the API answered "Verification email sent." having sent
  nothing, with no trace anywhere. `apps.accounts.mailer.send_transactional`
  reports whether the message was accepted and logs the cause when it was not.
- **`EMAIL_HOST` defaulted to `localhost`.** Nothing listens there, so a
  deployment that forgot the variable discarded every message — and if anything
  ever did listen, it would be the Mail Engine. There is now no default, and an
  unset or loopback host is detected and logged as an error.

Where the honest answer is available, it is given: `resend-verification`
returns 503 when sending failed, and invite creation reports
`email_delivered: false`. The password-reset endpoint deliberately does **not**
change its response, because a different answer for a registered address is an
existence oracle — that failure goes to the log.

Messages name MateMail / NetaMate and never the Mail Engine.

### Still required before this is production-complete

**No real delivery test has been performed, and this item must not be marked
complete until one has.** Deferred to the first suitable P4 milestone, where it
depends on the Mail Engine that P4 builds.

That test must verify actual inbox delivery from
`MateMail <noreply@mail.matemail.online>`, plus SPF, DKIM, DMARC where
applicable, PTR/HELO alignment, and no underlying engine branding leakage.
**SMTP acceptance alone does not count.**

Until then production keeps `EMAIL_HOST` unconfigured and
`MAIL_ENGINE_ADAPTER=stub`. The application refuses to send in that state rather
than reporting a false success, which is why it is safe to sit here.

---

## DKIM Private Key Storage

**Status: INTERIM mitigation (P3c). DEC-007r unchanged.**

P4 moves DKIM key generation and storage into the Mail Engine and removes
`Domain.dkim_private_key` from Django entirely. Nothing new should be built on
the assumption that Django holds these keys.

Until then, the column exists, and a DKIM private key is a domain's authority
to sign mail as itself — in a plain `TextField` it was readable from any
backup, replica or snapshot. It is now encrypted at rest:

- **Fernet** (AES-128-CBC + HMAC-SHA256) from `cryptography`, already a
  dependency. Authenticated, so a tampered value fails loudly instead of
  decrypting to garbage that would produce invalid signatures.
- **`DKIM_ENCRYPTION_KEY`, separate from `DJANGO_SECRET_KEY`.** `SECRET_KEY`
  signs sessions and JWTs and will be rotated for unrelated reasons; a rotation
  that made every DKIM key undecryptable would be discovered by customers, as
  mail failing authentication.
- **Versioned prefix** (`dkimv1:`) so rotation is possible and a row's state is
  identifiable rather than guessed.
- **Rotation**: `DKIM_ENCRYPTION_KEY` accepts a comma-separated list. The first
  encrypts; all are tried on read.

An unreadable stored value raises rather than returning `""` — an empty string
would read as "this domain has no DKIM key" and the domain would be provisioned
to send unsigned mail.

Migration `domains.0005` encrypts existing rows. It verifies each value
decrypts back to the original **before** overwriting it, skips rows already
encrypted, and is reversible. Rows written before the change are read as
plaintext transparently, so an upgrade cannot break signing for existing
domains.

If `DKIM_ENCRYPTION_KEY` is unset, storage degrades to plaintext with an error
log rather than refusing to add domains — and `manage.py check --deploy` fails
with `domains.E001`, so that state cannot reach production unnoticed.

The key is never exposed through the API, the serializer, or Django admin
(which shows presence only).

---

## Stale DKIM keys and cross-tenant inheritance

**Status: fixed in P4C-A. Found by measuring the real engine in P4B.**

The engine does **not** delete a domain's DKIM keypair when the domain is
deleted. The key outlives the domain, and re-registering the same domain name
adopts the surviving key.

Demonstrated end to end against the live engine:

1. Tenant A registers `example.com`; the engine mints a keypair.
2. Tenant A removes the domain. The domain record disappears. **The key does
   not.**
3. Tenant B legitimately acquires `example.com` and registers it.
4. Tenant B is silently issued **tenant A's private signing key**, and tenant
   A's selector rather than the one tenant B requested.

Domains change hands in the ordinary course of business, so this is not a
contrived scenario. Its effect is that a former owner retains the ability to
sign mail as the new owner's domain — with DNS that validates, because the
published record still matches the inherited key.

MateMail's own ownership verification (P3a) narrows but does not close this:
it ensures tenant B genuinely controls the domain, which is precisely the case
above.

**The fix.** Deprovisioning deletes the key explicitly, before the domain:

```
set_domain_active(False) → delete_dkim_key → delete_domain
```

Three properties make it hold rather than merely usually work:

- **Unconditional.** The key deletion runs even when the engine reports no such
  domain. "No domain, stale key" is exactly the state a previously failed
  cleanup leaves behind; short-circuiting on not-found would make the cleanup
  permanently unable to repair itself.
- **Key before domain.** Failing partway leaves an orphaned domain record, which
  is recoverable, rather than an orphaned signing key, which is the hazard.
- **Loud on failure.** A terminal failure raises instead of returning. A silent
  return would report clean removal over a usable key for a domain MateMail no
  longer controls.

Regression coverage is in `tests/test_dkim_lifecycle.py`, including a control
test asserting the hazard is still reproducible without the fix — if the engine's
behaviour ever changes, that test fails and tells us to re-check deprovisioning
rather than leaving dead defences in place.

---

## Mail Engine network exposure

**Status: CLOSED in P4C-A2.** Kept in full, because the rejected design looks
correct and is what most guidance recommends.

### What was wrong

The engine's private API and submission ports were published on MateMail's own
Docker bridge gateway, `172.24.0.1:8453` and `:587`, on the reasoning that a
private bridge address is private. They were never public — verified by scanning
the host from outside — but they were reachable from **every other Docker
network on the host**: `netamate_internal`, `matedesk_internal`,
`mateassist_internal`, `mateconnect_internal`, `portfolio_default` and the
default bridge all established TCP connections to both ports. Only
`matemail_internal` could not, and only because `internal: true` left it no
route at all.

**A published bind address selects a destination address. It never restricts the
source.** `docker-proxy` holds a userland socket on that address and accepts on
it whichever bridge the traffic arrived from.

The mechanism was confirmed with rule counters rather than inferred: during the
test the DNAT rule did not fire at all (25 → 25 packets) while the filter ACCEPT
did (87 → 90, exactly one per connection), and mailcow's own isolation rule saw
zero. So the traffic never traverses `DOCKER-USER`, and a rule there — the
obvious fix — would have done nothing. Filtering would have had to happen in
`INPUT`, a host firewall change, and the socket would still have been published.

The same re-origination made the engine see `10.244.0.1` as the client for every
request regardless of origin, which is why its `allow_from` ACL could not
identify the caller. **One root cause, two symptoms.**

### What replaced it

The socket was removed rather than filtered.

```
backend / celery-worker  ──▶  matemail_engine_link  ──▶  gateway  ──▶  engine
                              external, internal:true    TCP passthrough
```

- **A dedicated link network**, `matemail_engine_link`, declared `external` and
  created with `--internal`. Only `backend` and `celery-worker` join it.
- **One TCP passthrough gateway** (HAProxy 3.2 LTS, pinned by digest) owning the
  alias `mx.matemail.online` and forwarding 8453 to `nginx-mailcow` and 587 to
  `postfix-mailcow`. Layer 4 only: it does not terminate TLS, does not relay
  SMTP, and holds no key material.
- **No host port is published** on the application path. The engine's own
  loopback bindings remain for operator access over an SSH tunnel.

Measured after the change — the control passes and everything else fails:

| Source network | 8453 | 587 |
|---|---|---|
| `matemail_engine_link` (control) | reachable | reachable |
| `matemail_app` | blocked | blocked |
| `matemail_internal` | blocked | blocked |
| `netamate_internal` | blocked | blocked |
| `matedesk_internal` | blocked | blocked |
| `mateassist_internal` | blocked | blocked |
| `mateconnect_internal` | blocked | blocked |
| `portfolio_default` | blocked | blocked |
| default `bridge` | blocked | blocked |

The old `172.24.0.1` socket no longer exists and is unreachable from everywhere,
including the link itself.

### The boundary, stated precisely

| Layer | Control |
|---|---|
| Reachability | only containers on `matemail_engine_link` can open a connection at all |
| Network | the link is `internal`, so it is not a route to or from the Internet |
| Transport | TLS with hostname verification, terminated by the engine itself — the gateway passes it through untouched |
| Authentication | the engine's API key |
| Engine ACL | `allow_from` scoped to the gateway's pinned address, `10.244.0.247` |

The ACL identifies the **gateway**, not the calling container — the gateway
originates the connection, so that is the honest scope. It is defence in depth
behind reachability, not a substitute for it. `skip_ip_check` stays `0`.

---

## Domain deletion must not lose key custody

**Status: implemented in P4C-A2.**

The engine keeps a domain's DKIM keypair after the domain is deleted, so
`deprovision_domain_task` is what removes it. That makes queuing the task the
only thing standing between a domain deletion and an orphaned private key that
can sign mail as that domain.

The local `Domain` row is the only durable record that cleanup is owed. Delete
it while the task failed to reach the broker and nothing is left to reconcile:
no domain in MateMail, a live key in the engine, and no job anywhere that will
remove it.

**Rule: the local row is deleted only after the cleanup task is durably queued.**
If the enqueue fails the deletion is refused — the row stays, the API returns a
customer-safe `503` saying nothing was changed, and the failure is logged as a
security event. A customer who cannot remove a domain for a few minutes is a far
smaller problem than an orphaned signing key, and it is recoverable by retrying;
the orphan is not.

Two earlier versions of this code were both wrong: `except Exception: pass`
discarded the failure entirely, and its replacement logged the failure and
deleted anyway — recording the problem in a file nobody reads while creating
exactly the state above.

The task is enqueued **unconditionally**, not only when `mail_engine_provisioned`
is set: that flag is cleared at the start of a re-provision, so a domain can hold
engine state while the flag reads `False`. Deleting an absent domain and an
absent key both succeed, verified against the live engine, so the call costs
nothing when there is nothing to clean up.

The reverse ordering failure — task queued, local delete then fails — leaves the
row while the engine loses the domain. That is recoverable by reprovisioning and
is deliberately the preferred direction.

Covered by `tests/test_domain_delete_durability.py`.

---

## Secrets Management

| Secret | Storage |
|--------|---------|
| Django SECRET_KEY | Environment variable only, never committed |
| Database password | Environment variable |
| Mail Engine API key | Environment variable |
| DKIM private keys | ⚠️ **Plaintext in `Domain.dkim_private_key`.** This row previously claimed encryption via `django-encrypted-model-fields`; that package is not a dependency and the field is a plain `TextField`. Excluded from Django admin (P0). Target architecture is DEC-007r: the key is generated and stored inside the Mail Engine and never reaches Django. Interim mitigation in P3, column removed in P4. |
| JWT signing key | Environment variable |
| Password reset tokens | SHA-256 hashed before DB storage, raw token emailed once |
| 2FA backup codes | SHA-256 hashed before DB storage |
| Stripe API keys (Phase 11) | Environment variable |

**Never committed to git:**
- `.env` files
- Any file matching `*secret*`, `*password*`, `*key*` patterns
- `.gitignore` enforces this

---

## TLS Configuration

| Connection | Minimum TLS | Certificate |
|-----------|-------------|------------|
| HTTPS (443) | TLS 1.2 | Let's Encrypt auto-renew |
| IMAPS (993) | TLS 1.2 | Let's Encrypt auto-renew |
| SMTP TLS (465/587) | TLS 1.2 | Let's Encrypt auto-renew |
| Django to Mail Engine API | HTTPS via the HAProxy gateway on a dedicated private Docker network; certificate verified (DEC-014) | Let's Encrypt auto-renew |
| Mail Engine to SMTP policy bridge | HTTP over the internal network, authenticated by `INTERNAL_API_SECRET` | N/A |
| Django ↔ PostgreSQL | TLS or local socket | N/A |
| Django ↔ Redis | Local socket or TLS | N/A |

MTA-STS policy published for each customer domain enforces TLS on inbound mail.

---

## Domain Ownership Verification

**Status: IMPLEMENTED (P3a).**

Anyone can type a domain into the add-domain form. Before P3a, doing so queued
a mail-engine provisioning task, so an attacker could have a competitor's
domain configured for mail on our platform — enough to intercept mail if the
victim's MX ever pointed here, and enough to poison our sending reputation.

### The proof

Each domain is issued a random token (`secrets.token_urlsafe(32)`, prefixed
`matemail-verify-`) when it is added. The customer proves control by publishing
it:

| Field | Value |
|-------|-------|
| Type  | `TXT` |
| Host  | `_matemail-verify.<domain>` |
| Value | the issued token, exactly |

The check resolves that name and requires an **exact string equality** match
against one of the returned TXT strings. A substring or prefix match is
rejected: a shared zone where an unrelated record happens to contain the token
would otherwise verify. Multi-string TXT records are reassembled before
comparison, because resolvers split values longer than 255 bytes.

The token is not a secret from its own tenant — it has to be published in
public DNS. It is returned only through the tenant-scoped domain serializer, so
one tenant can never read another's token and pre-empt a claim.

### Exclusivity

A verified domain belongs to exactly one workspace. This is enforced in
Postgres, not in application code:

```sql
CREATE UNIQUE INDEX uniq_verified_domain_owner
    ON domains_domain (domain) WHERE ownership_status = 'verified';
```

Two tenants may both hold the same domain in `pending`; the first to verify
claims it, and the second's verification fails with a clear message. An
application-level check-then-save cannot close that window — two concurrent
verifications would both read "unclaimed" before either wrote. The claim runs
in a transaction and catches the `IntegrityError` the index raises.

Rotating a token returns the domain to `pending` and releases the claim. It is
a separate, admin-only endpoint rather than a side effect of a failed check,
because rotation invalidates the record the customer already published.

### What verification gates

Provisioning is refused for an unverified domain at four independent layers, so
a new caller cannot bypass the gate by reaching a lower one:

| Layer | Behaviour when unverified |
|-------|---------------------------|
| `POST /api/domains/` | Accepts the domain, issues a token, **queues nothing** |
| `POST /api/domains/:id/provision/` | `409` with a customer-facing message |
| `POST /api/mailboxes/` | `409`; no mailbox row is created |
| `POST /api/mailboxes/:id/reprovision/` | `409` |
| `provision_domain_task` (Celery) | Refuses at the task boundary, records the reason, does **not** retry |

The task-boundary check is the one that matters for future code: any caller
that reaches `provision_domain_task.delay(...)` directly is still stopped.

### Endpoints

| Endpoint | Role | Result |
|----------|------|--------|
| `POST /api/domains/:id/verify-ownership/` | support+ | `200` verified / `409` not yet |
| `POST /api/domains/:id/rotate-verification-token/` | admin+ | new token, back to `pending` |

Both write audit events (`domain_ownership_verified`, `domain_ownership_failed`,
`domain_token_rotated`).

---

## Abuse Prevention

| Threat | Control |
|--------|---------|
| Account takeover | 2FA with per-user throttling and TOTP replay prevention; password reset revokes every session (P3b) |
| Open relay | SMTP auth required; sender must equal the authenticated account; sender must be a mailbox MateMail hosts (P5) |
| Spam sending | Per-mailbox hourly and per-workspace daily limits, read from the plan and enforced atomically (P5). Reputation monitoring is **not** built — P7 |
| Anonymous signup reaching the Mail Engine | Workspaces start `PENDING_APPROVAL`; mail capability requires an `approved_at` granted by a named platform admin (P5) |
| A single compromised account | `set_mailbox_suspended` — a platform-only state a tenant admin cannot lift (P5) |
| A workspace sending abusively | `outbound_disabled` stops sending while leaving inbound and administration intact (P5) |
| Alias minting as cheap sending identities | `Plan.max_aliases`, enforced workspace-wide under a row lock (P5) |
| Credential stuffing | Per-IP and per-account login lockout on failures (P3b) |
| Tenant enumeration | Workspace slugs not enumerable via public API |
| Trial abuse / mass signup | 3 signups per hour per IP; `MAX_WORKSPACES_PER_USER` caps owned workspaces (P3b) |
| Over-privileged automation | API keys are read-only by default, scoped, and can never reach platform administration (P3b) |
| Mass mailbox creation | 20/hour per tenant plus `Plan.max_mailboxes` (P3b) |
| DNS abuse | DNS verification re-checks, domain pause on repeated failures |
| Domain hijack / squatting | TXT ownership proof required before provisioning; verified ownership is exclusive, enforced by a partial unique index (P3a) |
| Suspended tenant bypass | Two independent mechanisms: the policy bridge refuses, **and** `apply_tenant_suspension_task` deactivates the workspace's domains in the engine. Whether the engine-side half was queued is reported to the operator rather than logged and forgotten (P5) |
| Domain silently changing hands | Daily ownership re-verification; seven consecutive failures raise a flag for a human. Deliberately does not deprovision — see `docs/MAIL_POLICY.md` section 5 (P5) |

### Free `@matemail.online` accounts — a future threat surface, not a current one

**Nothing here is built. No free account exists.** Recorded so the controls
above are designed with this case in view rather than retrofitted around it.

DEC-015 puts free `username@matemail.online` mailboxes at P7.5, after the policy
framework (P5) and operational tooling (P7). The reason is a difference in kind,
not degree: **every control in the table above assumes an attacker who had to
prove control of a domain.** That assumption costs money, leaves a paper trail,
and makes a burned account expensive. A free mailbox costs a signup form.

What changes when that assumption goes away:

| Control above | Why free signup breaks it |
|---|---|
| Trial abuse / mass signup | Per-IP signup limits do not stop distributed automated signup, and there is no domain purchase to act as a cost floor |
| Spam sending | Per-tenant limits assume a tenant is a business; a free user is a tenant of one, so the same limit permits far more aggregate outbound |
| Domain hijack / squatting | Does not apply — but its replacement does: username squatting, and claiming addresses that appear to speak for MateMail |
| Reputation | Business tenants sending badly damage their own domain first; free users send from `matemail.online`, so damage is immediately shared with every other free user and with the platform |

Two requirements that follow directly:

- **Reserved addresses must exist before any username can be claimed.**
  `postmaster` and `abuse` are mandated by RFC 2142 and are how other operators
  report problems to us. `admin`, `administrator`, `support`, `security`,
  `billing`, `noreply`, `no-reply`, `hostmaster`, `webmaster` and `root` are
  addresses a recipient would reasonably read as speaking for MateMail — letting
  a stranger claim one hands them the platform's voice. Illustrative, not final;
  P7.5 defines the complete policy, including how the list is extended without
  breaking already-issued accounts.
- **Platform transactional mail stays on `mail.matemail.online`** (DEC-013) and
  must never be collapsed into the free-user domain. They have to be able to
  fail separately: a free user who gets `matemail.online` blocklisted must not be
  able to take password resets and verification mail down with them.

P5 designs these controls. P7.5 implements them. Neither is in scope before then.

---

## Audit Logging

All significant administrative events are written to `MailLog` with:
- `tenant`
- `event_type`
- `source` (user email or system)
- `ip_address`
- `result`
- `metadata` (JSON, no passwords or secrets)
- `created_at`

Events logged:
- User signup, login, logout, failed login
- Password reset requested / completed
- 2FA enabled / disabled
- Email verification
- Domain added / verified / paused / deleted
- Mailbox created / disabled / deleted / password reset
- Alias created / deleted
- Forwarding rule created / deleted
- DNS verification pass / fail
- Backup completed / failed
- Tenant plan changed
- Tenant suspended / reactivated
- Team member invited / removed
- Role changed
- API key created / revoked
- Platform admin action (separate audit log)

---

## No-Open-Relay Checklist

Two different things are being claimed here, and they are tracked separately on
purpose.

**A. MateMail decides correctly** — proved by `tests/test_smtp_policy_bridge.py`
against the real endpoints, with the real policy module behind them.

- [x] A sender MateMail does not host is refused
- [x] A sender on a domain MateMail does not host is refused
- [x] Sending as another tenant's mailbox is refused
- [x] Sending as a colleague in the same workspace is refused
- [x] Sending as the platform identity with a customer credential is refused
- [x] Case and whitespace do not defeat the sender comparison
- [x] Suspended workspace: outbound refused
- [x] Outbound-disabled workspace: outbound refused
- [x] Unapproved workspace: outbound refused
- [x] Suspended or disabled mailbox: outbound refused
- [x] Inactive sending domain: outbound refused
- [x] Inbound unknown domain: permanently rejected
- [x] Inbound unknown recipient: permanently rejected
- [x] The bridge refuses a missing, empty or wrong internal secret

**B. The engine is configured to ask** — proved by
`tests/test_engine_policy_integration.py`, which parses the real override and
evaluates it with a model of Postfix's own restriction semantics.

- [x] The policy hook is present in `smtpd_recipient_restrictions`
- [x] It precedes `permit_sasl_authenticated` — so authenticated submission
      reaches it instead of short-circuiting (this was blocker 2)
- [x] It follows `permit_mynetworks` — so the engine's own injections do not
      enter customer policy
- [x] It precedes `reject_unauth_destination`, and the bridge answers `DUNNO`
      rather than `OK`, so the anti-relay check is still reached
- [x] A second hook at `smtpd_end_of_data_restrictions` counts once per message
- [x] Every upstream restriction is preserved; sender, relay, client and milter
      settings are not redefined
- [x] The address Postfix dials equals the address Compose binds
- [x] The bridge publishes no host port and uses no host networking

**C. The bridge answers correctly over the wire** — proved by
`tests/test_policy_protocol_integration.py`, which runs the real daemon and
speaks the real protocol against a live MateMail. Cases A–L of the relay matrix,
including a MateMail outage (defers, never `OK`) and a wrong shared secret
(defers).

**D. Postfix actually invokes it in production** — **not yet.** This is the
remaining gap, and it closes at deployment, not in CI: Postfix is not running
here. `scripts/install-policy-bridge.sh` ends by printing the engine's own
effective `postconf smtpd_recipient_restrictions smtpd_end_of_data_restrictions`
and states what must appear in it.

- [ ] `install-policy-bridge.sh` run on the engine host
- [ ] effective `postconf` output shows the hook before `permit_sasl_authenticated`
- [ ] unauthenticated submission refused — verified against the live engine
- [ ] inbound unknown recipient refused — verified against the live engine

Until D is done, A–C are correct decisions nobody asks for. No public mail port
is open, so there is no live exposure, and the engine's own SASL requirement,
sender-login check and relay restrictions are what is holding — but this
checklist must not be read as finished.

---

## Native Engine NE3 — mail flow

NE3 is the phase where Postfix, Dovecot and Rspamd start reading NE2's
provisioning state. Everything below was verified against a running stack on
`.invalid` domains, not inferred from configuration.

**Status: implemented and validated locally. Not deployed to MateServer.**
mailcow remains the production engine and `MAIL_ENGINE_ADAPTER` is still
`mailcow`.

### Two defects found during NE3

Both were inherited, both looked healthy, and neither was visible from the
application side.

**1. Postfix could read every password hash.**

NE1's `postgres/init/001_bootstrap.sql` carried:

```sql
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO engine_ro_postfix, engine_ro_dovecot;
```

It was written as a safety rail — new tables start read-only — but it does the
opposite of least privilege. `ON TABLES` includes **views**, so every relation
created afterwards was granted to *both* reader roles automatically, whatever
the migration's own `GRANT` statements said. `engine_ro_postfix` could therefore
`SELECT password_hash FROM dovecot_auth`.

Demonstrated rather than argued: under the NE1 rule a freshly created view came
out carrying `engine_ro_postfix=r , engine_ro_dovecot=r`. The rule had already
fired once — migration 002 needed an explicit `REVOKE SELECT ON dkim_key` to
claw back access it never granted — and any future table would have been exposed
the same way.

Migration 003 retires the rule (`ALTER DEFAULT PRIVILEGES ... REVOKE`) and
revokes the grants the six new views had already inherited, then grants each
role only what it needs. A new relation is now readable by nobody until a
migration says otherwise.

Regression: `ReaderPrivilegeTest` in
`backend/tests/test_native_engine_mail_flow.py`, including a canary that creates
a view and asserts it has no grantees.

**2. Antivirus detected malware and delivered it anyway.**

Rspamd ships `CLAM_VIRUS` with a score of `0.00`. An EICAR probe through the
full NE3 path produced:

```
CLAM_VIRUS(0.00){Eicar-Test-Signature;} ... [11.40/15.00] add header
```

ClamAV identified it correctly, the message scored below the reject threshold,
and it landed in the recipient's Maildir. An antivirus that scans, identifies
malware and hands it to the customer is worse than none, because monitoring
shows it working.

`rspamd/local.d/groups.conf` now scores a confirmed virus far above any
threshold. The same probe afterwards produced `[2011.40/15.00] reject` and
`554 5.7.1` at SMTP time, with nothing delivered. A scanner *failure* is scored
separately at zero — a broken scanner is not the same as dirty mail, and Postfix
already defers when Rspamd itself is unreachable.

### The read contract

Postfix and Dovecot read six views (migration 003), never a base table:

| View | Reader | Purpose |
|------|--------|---------|
| `postfix_virtual_domain` | postfix | is this domain ours |
| `postfix_virtual_mailbox` | postfix | does this mailbox receive |
| `postfix_virtual_alias` | postfix | where mail is delivered |
| `postfix_sender_login` | postfix | **who may send as what** |
| `dovecot_auth` | dovecot | password hash only |
| `dovecot_userdb` | dovecot | mail location and quota |

Neither role can read the other's views, neither can reach a base table, neither
can write anything, and neither can create anything. Verified by connecting as
each role: 19 of 19 checks behave as intended.

`postfix_sender_login` is the anti-spoofing control. Ownership comes from
exactly two places — a mailbox owns its own address, and an alias whose
destination *is* a hosted mailbox is owned by that mailbox. It does not
reference the `forwarding` table at all, so pointing forwarding at an address
can never confer the right to send as it; an external alias destination has a
NULL `mailbox_id` and is excluded by an INNER JOIN. Both exclusions are
structural rather than filtered, so a later WHERE-clause edit cannot admit them.

### Negative results, measured

| Attempt | Result |
|---|---|
| authenticated user spoofs another mailbox | `553 5.7.1 not owned by user` |
| authenticated user spoofs another tenant's domain | `553 5.7.1` |
| authenticated user sends as their internal alias | accepted (correct) |
| authenticated user sends as an alias pointing outside | `553 5.7.1` |
| unauthenticated relay to the Internet | `554 5.7.1 Relay access denied` |
| unauthenticated inbound to a real mailbox | accepted and delivered |
| unauthenticated inbound to an unknown mailbox | `550 5.1.1 User unknown` |
| authentication as an alias rather than a mailbox | `535 5.7.8` |
| suspended mailbox or domain attempts to send | `535 5.7.8` at authentication |
| EICAR probe | `554 5.7.1`, not delivered |
| delivery exceeding a mailbox quota | LMTP `Quota exceeded`, not stored |

No mail left the engine: the Postfix queue stayed empty and every `relay=` in
the logs pointed at Dovecot.

### Secrets

Neither daemon can read a secret from its own configuration environment, so both
images render credentials at container start and nothing is committed.

Dovecot's `%{env:...}` is a particular trap: it parses, `doveconf -n` echoes it
back unexpanded, and libpq then reports `fe_sendauth: no password supplied`. The
result is a configuration that looks correct and authenticates nobody. Both
entrypoints refuse to start without their credential rather than coming up
healthy and failing every lookup, and both reject a password containing
characters that cannot be represented in their config format — `#` opens a
comment and a newline ends a setting in both.

Dovecot holds a **separate** credential for reporting logins to the engine API
(`X-Native-Policy-Secret`), scoped to that one endpoint. Handing it the
provisioning secret would let the most network-exposed component in the engine
create domains, mint mailboxes and rotate DKIM keys. Verified in both
directions: the provisioning secret is refused at the policy endpoint, and the
policy secret is refused at `/v1/domains/ensure`.

### DKIM signing

The selector is resolved per domain from a map the engine API publishes from the
`dkim_key` rows. The previous `selector = "mm1"` was correct only for domains
that had never rotated; for any other it would look for a key file that does not
exist, and either send unsigned or sign with a key the published DNS record does
not name. A signature that fails to verify is worse than no signature, because
it reads as a forgery.

There is deliberately no fallback selector. A domain missing from the map is a
provisioning bug, and unsigned mail that shows up in monitoring is a better
outcome than mail signed with a guess.

Rspamd mounts the key store read-only and the API remains its only writer.
Postfix cannot see the directory at all — `ls /var/lib/rspamd/dkim` inside the
Postfix container returns "No such file or directory".

The signing chain was verified cryptographically rather than by inspecting
headers: a message from the real flow verified with `dkimpy` against the public
material the API publishes, before and after a selector rotation, and failed
against an unrelated key and against the previous selector's key. Details and
the rotation propagation window are in `docs/NATIVE_MAIL_ENGINE.md`.

A selector change does not delete the outgoing key; it retires it, keeping the
file under its exact name for 1800 seconds so an Rspamd worker still holding the
previous selector map signs correctly instead of sending unsigned. The retained
key stays 0600, owned by the engine's DKIM uid, is never returned through the
API, never becomes authoritative again, and is removed by reconciliation once the
grace expires. Rationale and the measured propagation bound are in
`docs/NATIVE_MAIL_ENGINE.md`.

No API response carries private material. Probed across `/v1/dkim` (including
`?include_private=1` and `?private=true`), `/v1/domains`, `/v1/mailboxes` and
`/status`: zero hits for private-key material, and `FORBIDDEN_RESPONSE_KEYS`
refuses to serialise such a field at all.

---

## Native Engine NE4 — operations

**Status: implemented and validated locally. MateServer runtime validation
pending.**

### The defect NE4 exists to fix

NE3 runtime validation found that `delete_mailbox` removed a mailbox's rows and
LEFT ITS MAIL on disk, at a path derived from the email address. Recreating that
address pointed the new owner at the previous owner's mail. No attacker is
required — only staff recreating a mailbox somebody asked to have deleted.

Migration 004 addresses storage by an opaque identity minted once per mailbox
row. A recreated address is a new row, so it gets a new identity and a different
directory; the old one becomes unreachable by construction rather than by
remembering to delete it. Proven end to end: after delete and recreate, the new
mailbox reported 0 messages while the old directory was still on disk and
recorded in `retired_mailbox_storage`.

Deletion keeps the mail deliberately. A provisioning call must not destroy
customer data as a side effect; P6 owns retention and removal, and the table
gives that work a list to act on rather than a directory tree to interpret.

### Operational control paths

| Path | Who may call it | Credential |
|---|---|---|
| queue / quarantine | Native API only | `NATIVE_CONTROL_SECRET` |
| rate-limit policy | this container's own smtpd | loopback only |
| mailbox usage | Native API only | `NATIVE_DOVEADM_API_KEY` |

The queue control plane runs INSIDE the Postfix container. The Native API never
receives the Docker socket, never mounts the spool, and Django never runs
`docker exec`. The daemon runs no shell: every Postfix invocation is a fixed
argv list whose only caller-supplied element is a queue id already matched
against `^[A-Za-z0-9]{6,32}$`. Traversal and injection attempts are refused with
`400` before reaching the process that could act on them.

Release is `postsuper -H`, which cannot alter a sender, a recipient or the
content — so releasing quarantined mail is structurally incapable of
redirecting it. Verified: a released message arrived exactly once with its
`Delivered-To` unchanged.

### Failure behaviour, measured

| Component down | Behaviour |
|---|---|
| Rspamd | `454 4.3.0` — submission deferred, nothing accepted unscanned |
| Dovecot | `454 4.7.0` — SASL unavailable, so nothing is accepted at all |
| PostgreSQL | `451 4.3.0` — lookups defer; no open relay, no spoof bypass |
| Unbound | `451 4.3.0` — DNS-dependent mail defers rather than being lost |
| ClamAV | `soft reject` via `CLAM_VIRUS_FAIL` — deferred, never treated as clean |
| Redis | `4.7.1` — submission DEFERRED for mailboxes that have a limit |
| Native API | mail path unaffected; Postfix and Dovecot read PostgreSQL directly |

Two of these deserve the reasoning spelled out.

**The rate limiter fails CLOSED when the counter is unavailable.** An earlier
NE4 build allowed the message, which meant a Redis outage silently disabled
every configured limit: a mailbox somebody had deliberately capped was accepted
with no enforcement at all, indistinguishable from having no limit. It now
returns `4.7.1` and the sender retries.

The distinction that keeps this from becoming a wider outage is WHERE the
decision is made. A mailbox with no configured limit — or an explicit
`messages = 0` — returns before Redis is contacted at all, so an outage cannot
block traffic that was never being metered. Measured with a live engine:

```
Redis reachable     limited@ allowed        unlimited@ allowed
Redis unreachable   limited@ DEFER 4.7.1    unlimited@ allowed
Redis restored      limited@ allowed        unlimited@ allowed
```

Recovery needs no intervention: nothing is cached or latched, so the next
message re-runs the same lookup and counter.

The LOOKUP failing is deliberately treated differently from the COUNTER
failing. That path means the engine database is unreachable, and in that state
Postfix's own `virtual_mailbox_maps` and `smtpd_sender_login_maps` lookups are
failing too — submission is already deferred by the restrictions that run first
(measured: `451 4.3.0 Temporary lookup failure`, with no relay and no spoof
bypass). It also cannot be made strict safely: with no database there is no way
to know which mailboxes have a limit, so failing closed there would block every
mailbox including the ones nobody ever metered.

Postfix still defers if the policy service cannot be reached at all
(`smtpd_policy_service_default_action`), so a missing limiter is not a free pass
either.

**A scanner that could not answer is not a clean verdict.** Rspamd scores
`CLAM_VIRUS_FAIL` at zero by default, so an unreachable ClamAV produced mail
that flowed through unscanned and looked exactly like mail that had been checked.
`force_actions.conf` now defers instead. Configuring `symbol_fail` on the
antivirus rule was required for the symbol to exist at all — without it the
module logs an error and emits nothing.

Rate-limit rejection is `4.7.1` through `DEFER_IF_PERMIT`. A limit is a
statement about timing, not about the message: a permanent rejection would
destroy mail that was never wrong and bounce a throttling decision.

### Deployment correctness

Compose does not recreate a container when only a bind-mounted configuration
file changed. NE3 lost five days of antivirus enforcement to this. `deploy.sh`
hashes each service's configuration directory and passes the hash in the
environment, so a configuration change becomes a change Compose can see.

---

## Native Engine NE5 — control-plane switch

**Status: COMPLETE — switched on MateServer 2026-09-19 (release 37ebb217).**

MateMail's control plane moves to `MAIL_ENGINE_ADAPTER=native`. Internet mail,
the platform sender and every public port are untouched — NE6 owns those.

### The boundary

MateMail reaches exactly one Native Engine component, the API, over
`matemail_engine_link` (internal, no egress, externally owned). Postfix,
Dovecot, Rspamd, the engine database and the DKIM key store are unreachable from
the application, so a compromised MateMail can ask the API to provision — under
every check the API makes — and cannot touch the mail path or the mail store.

### Failure behaviour

An unreachable Native API raises `EngineUnavailable`, which is retryable, and
**never** silently becomes a mailcow call. Verified by stopping the API and
attempting a real provisioning call through the application: the operation
failed retryably, the adapter was still Native afterwards, and mailcow received
nothing. After restarting the API the same operation succeeded and produced no
duplicate.

That matters more than it looks. A fallback would provision a customer into
mailcow while MateMail believed it was using Native, and the two would diverge
silently — with no error anywhere to notice.

### Configuration that cannot half-work

`MAIL_ENGINE_ADAPTER` accepts only `stub`, `mailcow` or `native`; anything else
raises rather than falling back to the stub, which accepts every operation and
reports success without an engine. `manage.py check --deploy` additionally
refuses a `native` deployment with a missing URL or secret, or a URL pointing at
loopback — which is this container, never the engine.

### Transactional mail is a separate switch

`EMAIL_HOST`, `EMAIL_PORT` and the platform sender are independent of
`MAIL_ENGINE_ADAPTER`; no setting derives one from the other, and the adapter
contract has no method that sends a message. Password resets and verification
mail continue to leave through the existing path until NE6 moves them
deliberately.

### Rollback

`MailcowAdapter` remains present and configured, and mailcow keeps running.
Reverting the control plane is an environment variable and a restart.

---

## Backups (P6)

**Encryption.** Snapshots are restic repositories: AES-256 with Poly1305-AES
authentication. No custom cryptography exists anywhere in the backup system.

**The repository password is the whole security boundary.** It lives at
`/opt/MateMailBackup/restic-password`, root:root 0600, is generated once by
`install.sh` and is never printed, logged or committed. `install.sh` refuses to
overwrite an existing one, because doing so would make every existing snapshot
permanently unreadable. A copy must be held off the host; without it the
backups cannot be decrypted by anyone.

**Unencrypted material never reaches a disk.** Database dumps, DKIM private
keys and the copied `.env` files are staged under `/run` — tmpfs, root-only —
and are wiped on exit whether the run succeeded or failed. They go from there
into the encrypted repository and nowhere else.

**Database credentials never cross onto the host.** `pg_dump` runs inside the
database container and reads `$POSTGRES_USER` / `$POSTGRES_DB` from the
container's own environment, so no credential appears in a host process
listing. restic is given `--password-file`, a path, never `--password`.

**What the snapshots contain.** Both `.env` files are in every snapshot,
because a deployment cannot be rebuilt without them. This makes the repository
as sensitive as production: it holds the Django secret key, the database
passwords, the Native API secret and the DKIM private keys. Its directory is
0700 and its contents are encrypted at rest.

**Restore cannot destroy mail.** `matemail-restore-mailbox.sh` refuses to write
into a mailbox that holds messages unless told twice, and even then moves the
existing Maildir aside rather than deleting it (DEC-033). The restore drill
refuses a `--workdir` naming any production path, and loads dumps into a
throwaway PostgreSQL container on `--network none`, which is why trust
authentication is acceptable there and nowhere else.

**Verified, not assumed.** Each run lists the snapshot it just wrote and fails
if the dumps, the manifest or the mail storage are absent; `restic check` runs
every time. The drill verifies every staged file against the SHA-256 recorded
when it was written, checks restored row counts against the manifest, and
requires each DKIM key to parse as a usable key. A snapshot whose manifest
records no expectations fails the drill rather than passing it.

---

## Monitoring (P7)

**Nothing is publicly reachable.** Prometheus, Alertmanager, Grafana and
node-exporter are bound to `127.0.0.1` only. No nginx route is added for any of
them and no monitoring domain exists. Operator access is an SSH tunnel by
someone who already has root. Monitoring data is itself sensitive: it says
which services are down, when backups last ran and what is exposed.

**No container is given the Docker socket.** Mounting `/var/run/docker.sock`
into a container is handing it root on the host, which is why there is no
cAdvisor here. Container and host state is gathered by one root-owned collector
running on the host from a systemd timer, which publishes plain text that
node-exporter serves. The monitoring containers hold no privilege at all;
node-exporter additionally reads `/proc` and `/sys` read-only and cannot act on
anything.

**No credentials in metrics, and none on a command line.** The collector queries
each database inside its own container so `$POSTGRES_USER` and `$POSTGRES_DB`
come from the container's environment and never appear in a host process
listing. The Native API is probed from inside the API container using its own
environment secret. restic is given a password *file path*, never a password.
No password, API secret, DKIM private key, message body, mailbox name or
recipient appears in any metric, label, dashboard or log line.

**Metric labels are a bounded allowlist**, enforced by a test that reads the
collector's syntax tree (DEC-036). This is both a privacy control and a
cardinality control: a label built from a Dovecot log line would publish
customer addresses into the metrics store *and* create one time series per
address.

**Grafana** has sign-up disabled, anonymous access disabled, and analytics and
update checks off. Its admin password is generated at install, stored in a
root-only `0600` runtime `.env`, never printed and never committed. Re-running
the installer will not overwrite it.

**Detection only.** Monitoring verifies the firewall, the published ports, the
listeners, DNS, PTR and the certificate — and changes none of them. A
monitoring system that rewrites firewall rules is one that can lock an operator
out of the host at three in the morning.

**Alerting does not depend on the mail system it watches** (DEC-037). There is
no SMTP configuration in Alertmanager at all.

**Log growth is bounded.** P7 found Docker running with no `daemon.json` and
therefore no log rotation whatsoever, with individual container logs already at
45 MB. `/etc/logrotate.d/matemail-docker-containers` caps them without requiring
a Docker daemon restart across 72 containers.

---

## Platform outbound mail (NE6)

**MateMail reaches exactly two things in the engine.** The control-plane API
and a TCP forwarder on port 587. Verified from the running backend: Native
PostgreSQL, Dovecot, Redis, Rspamd and Postfix-direct do not even resolve.

**The gateway holds nothing worth stealing.** `mode tcp`, read-only
filesystem, no credential, no key, one listener, one upstream. The STARTTLS
session and the SASL exchange run end to end between Django and Postfix, so a
compromise of the gateway yields ciphertext.

**It is not trusted by address.** `mynetworks` is host-local only, so the
gateway must authenticate like any other client. Had it grown to include the
container subnet, `permit_mynetworks` would have let anything reaching the
gateway relay to the Internet unauthenticated — and the gateway is reachable by
the whole application. A test pins this.

**Verified refusals, from the real runtime:** wrong password → 535;
unauthenticated relay → 554; authenticated but sender not owned by the login →
`553 5.7.1 not owned by user`. Platform mail is not privileged: it passes the
same STARTTLS, SASL, sender-ownership, rate-limit and scanner checks customer
mail will.

**TLS is verified, not assumed.** Django's SMTP backend uses
`ssl.create_default_context()`, so the certificate and its hostname are both
checked. Nothing was weakened for NE6 — instead the real Let's Encrypt
certificate was installed into the engine, because the TLS volume was empty and
submission required STARTTLS. One certificate is copied in; `/etc/letsencrypt`
is not mounted, as it holds every other application's private keys.

**The DKIM exception is bounded.** One private key was imported, once, by a
script that reads it on stdin — never a command line, environment variable or
shell history — refuses anything whose public half does not match DNS, and was
deleted afterwards. No HTTP route accepts private keys, and a test asserts none
exists (DEC-039). The key file is 0600 and no branch of the utility can print
key material; a test drives every failure path and scans all output for it.

**No automatic failover.** If Native submission is unavailable MateMail fails
and retries; it does not route through Mailcow. Proven by injection: with the
gateway stopped, the send failed and Mailcow's log gained zero lines. A silent
failover would have changed the scanning and rate-limiting properties of
outbound mail without anyone choosing that (DEC-040).

**Unchanged by NE6:** UFW, DNS, PTR, public listeners, published ports. Zero
mail ports are publicly reachable; that remains NE7's decision to make
deliberately.
