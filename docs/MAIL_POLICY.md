> **CURRENT DOCUMENTATION NOTICE (2026-10-09):** Policy development ledger. Production Native Engine is already live; earlier Mailcow-specific policy examples and sender identities are archival. Current sender domain is `mail.matemail.pro`.
> Authoritative current references: [Architecture](ARCHITECTURE.md),
> [Deployment](DEPLOYMENT.md), and [Backup/Azure DR](BACKUP_RESTORE.md).
> Sections below may describe historical migration states or retired domains.

---

# MAIL_POLICY.md — Sending policy, deliverability and abuse response

**Product:** MateMail
**Status:** current as of P5 (2026-09-12)
**Authority:** DEC-017 (policy framework), DEC-018 (free accounts, design only)

This document describes what MateMail permits, what it refuses, and what an
operator does when something goes wrong. It is the operational companion to
`docs/SECURITY.md`, which covers isolation and authorization, and to
`docs/MAIL_ENGINE.md`, which covers the adapter boundary.

---

## 1. The lifecycle a workspace passes through

```
signup
  └─> PENDING_APPROVAL          can sign in, configure, see the product
        │                       CANNOT provision anything into the Mail Engine
        │
        ├─ approve_tenant ──> TRIAL / ACTIVE  + approved_at set
        │                       │
        │                       ├─ add domain
        │                       ├─ prove ownership (DNS TXT)
        │                       ├─ provision into the engine  ← first engine contact
        │                       ├─ create mailboxes
        │                       └─ send, within the plan's limits
        │
        └─ reject_tenant ───> REJECTED        approval revoked, nothing deleted
```

Two facts are required for mail capability, and both are checked every time:

1. status is `trial` or `active`, and
2. `approved_at` is set.

The second is what makes approval a gate rather than a convention. A status can
be set by a fixture, a shell, or a future admin action that forgets; the
approval timestamp carries a name and a time.

**Everything fails closed.** Unknown status, missing plan, unreachable rate
limiter, tenant that cannot be loaded: denied.

---

## 2. Sending limits

Limits live on `Plan` and are enforced by the SMTP policy bridge at submission.

| Tier | Aliases | Messages/hour/mailbox | Messages/day/workspace |
|---|---|---|---|
| trial (Private Beta) | 50 | 50 | 500 |
| starter | 100 | 100 | 1,000 |
| business | 500 | 200 | 5,000 |
| infrastructure | 2,000 | 300 | 20,000 |
| *(no resolvable plan)* | — | 20 | 100 |

A workspace with no plan gets the tightest values of all. Absence of a plan is a
configuration gap, and the safe reading of a gap is the tighter one.

**Two independent limiters exist, on purpose.** MateMail's own (above) is
consulted by the policy bridge and can be reasoned about in product terms. The
Mail Engine holds a per-mailbox limit of its own, which applies even to a client
that somehow reaches submission without passing through MateMail.

**Raising a limit** is a `Plan` change, effective on the next submission. No
deployment is required. Counters are hourly and daily with a TTL, so an
adjustment is visible immediately for new sends and existing counters expire on
their own.

### The platform sender

`noreply@mail.matemail.online` has its own hourly ceiling
(`PLATFORM_SENDER_MAX_PER_HOUR`, default **60**). Trusted to send is not the same
as trusted to send without bound; the realistic failures are a leaked credential
and a retry loop, and a ceiling contains both.

The number matches the limit the Mail Engine already holds on that mailbox —
measured as 60/hour against the live engine, not assumed. The two must agree,
and MateMail's must not be the higher of the pair: a MateMail limit above the
engine's would never bind, so a looping sender would meet the engine's hard
rejection instead of MateMail's DEFER, and a retryable condition would present
as a permanent failure on a password reset.

---

## 3. What the bridge answers, and why

`REJECT` is a permanent failure (5xx): the sending server gives up and returns
the message. `DEFER` is temporary (4xx): the sending server holds it and retries
for several days.

### Inbound

| Condition | Answer |
|---|---|
| domain not hosted here | REJECT |
| mailbox does not exist | REJECT |
| workspace cancelled or rejected | REJECT |
| workspace suspended, past due, or pending approval | DEFER |
| mailbox suspended or disabled | DEFER |
| domain inactive | DEFER |
| MateMail cannot determine the answer | DEFER |

The principle: **refuse permanently only when the answer will not change.** A
suspension lifted on Monday should deliver Friday's mail, not discover it was
bounced. Conversely, a nonexistent address must reject — deferring would make
MateMail a backscatter source and hide typos from senders for days.

A workspace with outbound disabled still **receives** normally. Cutting inbound
as an abuse response punishes the people writing to the customer and loses mail
that was never the problem.

### Outbound

Outbound refusals are permanent, including for reasons inbound defers. The
asymmetry is deliberate:

- inbound mail belongs to a third party who should not lose it over our
  customer's billing problem;
- outbound mail belongs to the customer, who is at a mail client and is better
  served by an immediate, clear refusal than by silent queuing;
- deferring a suspended workspace's outbound would accumulate a spool of exactly
  the mail we suspended them for, and release it when the suspension lifted.

The single exception is a **rate limit**, which defers: the sender is within
policy and merely early.

---

## 4. No open relay, and no sending as someone else

Everything the outbound policy decides is decided about the mailbox that
**authenticated** — `sasl_username`, which Postfix sets from the SASL layer —
never about the address in MAIL FROM, which the client chooses. Looking policy
up by envelope sender would let an attacker pick which record their own request
is judged against.

The authenticated account may use two envelope senders, and no others:

1. **itself** — `sender == sasl_username`;
2. **an alias it is explicitly authorized for** — an active `Alias` whose
   `destination_mailbox` is that mailbox.

The alias rule mirrors what the engine itself enforces at MAIL FROM through
`reject_authenticated_sender_login_mismatch` and its sender ACL, where an alias
whose `goto` is the logged-in mailbox is a permitted sender. The two must agree:
MateMail being stricter would refuse alias mail the engine had already accepted,
and being looser would assert a policy the engine then refuses.

Because an alias belongs to a domain, and a verified domain belongs to exactly
one tenant, there is no query shape here that reaches across tenants. An alias
on another customer's domain grants nothing, and neither does an active alias in
the same workspace that delivers to a colleague rather than to the authenticated
mailbox.

Anything else — an address MateMail does not host, another tenant's mailbox, a
colleague's mailbox, the platform identity — is refused. Cross-tenant spoofing
is the multi-tenant form of an open relay: one customer sending as another
customer's domain, from infrastructure that would DKIM-sign it.

Rate limits are charged to the **authenticated mailbox**, never to the envelope
sender, so an account cannot spread its hourly quota across every alias it
holds.

The platform identity is checked the same way and allowed no further: the
allowance is keyed on the authenticated account and then requires the sender to
equal it exactly, so a customer credential cannot claim it and the platform
credential cannot send as anyone else. It is one exact address, never a domain
or a subdomain — `anything@mail.matemail.online` would hand the exemption to
every future mailbox on the platform's own sending domain.

### How the engine asks

Postfix cannot call an HTTP API, so the integration is a policy bridge — a
sidecar container that speaks Postfix's `check_policy_service` protocol and
translates it to MateMail's internal endpoints. It carries no policy of its own.

The hook is installed at two stages, and the split is deliberate:

| Postfix stage | Question | Counts? |
|---|---|---|
| `smtpd_recipient_restrictions` | authorize (`stage=rcpt`) | no |
| `smtpd_end_of_data_restrictions` | authorize and record (`stage=end_of_data`) | yes, once |

`RCPT` fires once per recipient, so counting there would charge a sender five
messages for one message to five people. `END-OF-MESSAGE` fires once per
message. The recipient hook exists to refuse a bad submission before the body is
transferred; the end-of-data hook is the authoritative one.

**Ordering is the whole of it.** In the recipient chain the hook sits *after*
`permit_mynetworks` and *before* `permit_sasl_authenticated`:

```
check_recipient_mx_access …,
permit_mynetworks,                          ← engine's own injections settle here
check_policy_service inet:10.244.0.246:10031,   ← MateMail
permit_sasl_authenticated,
check_recipient_access …,
reject_invalid_helo_hostname,
reject_unauth_destination                   ← anti-relay, must still be reached
```

After `permit_sasl_authenticated`, Postfix would stop evaluating the moment a
client authenticated — so every authenticated submission, exactly the traffic
MateMail needs to rule on, would short-circuit before reaching the hook. It
would be installed, running, and never asked. That was "blocker 2".

Before `permit_mynetworks`, the engine's own unauthenticated injections —
watchdog probes, quarantine digests — would be put to customer policy, which
knows nothing about them.

**The bridge answers `DUNNO`, never `OK`.** In Postfix a policy service's `OK`
means "permit and stop evaluating this list" — which would skip
`reject_unauth_destination`, the anti-relay check that follows. `DUNNO` means
"no objection, carry on", leaving every later restriction in force.

**A MateMail outage defers, it does not pass.** The bridge answers
`DEFER_IF_PERMIT`, so mail is retried rather than sent unchecked or bounced
permanently. `DEFER_IF_PERMIT` rather than a bare `DEFER` so that a relay
attempt a later restriction would reject outright is still rejected during an
outage.

### What the engine keeps enforcing on its own

Nothing upstream was removed or relaxed. In particular:

| Engine control | Why it stays |
|---|---|
| `reject_authenticated_sender_login_mismatch` + `smtpd_sender_login_maps` | the engine's own anti-spoofing, at MAIL FROM — earlier than any policy service can be consulted |
| `smtpd_relay_restrictions` (`defer_unauth_destination`) | evaluated independently; makes unauthenticated relay impossible even while MateMail is down |
| `smtpd_client_restrictions` on 587 | refuses an unauthenticated client outright |
| Rspamd, postscreen, DNS blocklists, TLS policy | MateMail rules on identity and product policy; the engine rules on content, reputation and transport |
| per-mailbox engine rate limit | holds even for a client that somehow reaches submission without MateMail |

### Status

Implemented in the repository and covered by tests
(`test_engine_policy_integration.py` for the configuration and ordering,
`test_policy_protocol_integration.py` for the wire protocol end to end).

**Not yet deployed.** Until `scripts/install-policy-bridge.sh` has been run on
the engine host, the engine still does not consult these decisions at submission
time. No public mail port is open, so there is no live exposure; the engine's
own SASL requirement, sender-login check and relay restrictions are what is
holding in the meantime.

---

## 5. Domain ownership

Ownership is proved by a `_matemail-verify` TXT record before a domain is
provisioned, and the check is enforced at all four paths that can reach the
engine: the create view, the manual provision endpoint, the Celery task, and
mailbox creation.

Exclusivity is a **partial unique index** — at most one verified row per domain
name across every tenant — not an application check, because two tenants
verifying concurrently would both pass an application check and both commit.

Several tenants may hold a *pending* claim on the same name. That is the only
way an honest customer can claim a domain a squatter has already typed in.

### Re-verification

A daily sweep re-checks every verified domain and counts consecutive failures.
Seven in a row raises `DOMAIN_OWNERSHIP_STALE` once, and a human decides.

It deliberately deprovisions nothing. The signal is the absence of a DNS record,
and DNS is absent for many reasons that are not a change of ownership — a
resolver outage, a registrar migration, a customer tidying records they were told
were one-time. Cutting off a legitimate customer over a bad week of lookups
would do more damage, more often, than the case being defended against.

**What a stale flag means.** Most often, nothing: the customer removed a record
they thought was single-use. Occasionally it means the domain has changed hands,
in which case the former tenant still holds the verified row and still receives
the mail, and the new owner cannot claim it at all. That case needs a human to
contact both parties; there is no safe automatic resolution.

---

## 6. Abuse response

Reach for the narrowest response that stops the damage.

| Response | Endpoint | Scope | Customer keeps |
|---|---|---|---|
| Suspend one mailbox | `POST /api/platform/mailboxes/{id}/suspend/` | one account | everything else |
| Disable outbound | `POST /api/platform/tenants/{id}/outbound/` | one workspace's sending | inbound, settings, administration |
| Suspend the workspace | `POST /api/platform/tenants/{id}/suspend/` | everything | its data |

All three are reversible, require an authenticated platform admin, take a row
lock, and write an audit event with the actor and reason.

### Two things to check when you act

**Suspending a workspace returns `engine_applied`.** Suspension is two
mechanisms — the database row and the engine-side domain deactivation — and the
customer stops sending if either works. If `engine_applied` is `false`, the
engine was **not** told: the Celery broker could not be reached. The workspace is
suspended in MateMail, but its domains may still be accepting submission. Requeue
or investigate before considering the incident contained.

**Mailbox suspension is all-or-nothing.** If the engine refuses, the whole
transaction rolls back and you get an error rather than a database row claiming
a suspension that did not happen.

### What suspension does not do

- It does not delete anything. Suspension is reversible by design.
- It does not revoke approval. A billing suspension must not cost the customer a
  fresh approval; `can_use_mail` denies on status alone.
- It does not stop inbound unless you suspend the whole workspace.

### Reactivation

`POST /api/platform/tenants/{id}/activate/` refuses a workspace that was never
approved, and says so. Approving is the right door; activating a pending
workspace used to succeed and do nothing visible.

---

## 7. Deliverability

MateMail's sending reputation is shared by every customer, which makes it a
platform asset rather than a per-customer one. That is the reason for most of
the conservatism above.

### What is in place

| Control | State |
|---|---|
| SPF, DKIM, DMARC on `mail.matemail.online` | published; verified passing in P4 |
| DKIM per customer domain, 2048-bit | engine-generated, engine-held (DEC-007r) |
| PTR / forward-confirmed reverse DNS | aligned with `mx.matemail.online` |
| Per-mailbox and per-workspace send limits | plan-driven, enforced |
| Engine-side per-mailbox limit | independent second limiter |
| Message-ID rooted at the sending domain | P5 |
| Outbound blocked for unapproved workspaces | P5 |

**DMARC is at `p=none` deliberately.** It is a monitoring posture: reports
arrive, nothing is rejected on our say-so. Moving to `quarantine` or `reject` is
a decision to make after observing real traffic, not before.

### What is not in place

- **No bounce processing.** Bounces addressed to the platform sender are
  rejected, because `mail.matemail.online` hosts no mailbox. Delivery failures
  are therefore not currently visible to MateMail as data. This is a monitoring
  gap, not a delivery gap, and belongs with P7.
- **No feedback-loop registration** with major providers.
- **No reputation monitoring or blocklist alerting.** P7.
- **No outbound content scanning** beyond what the engine's own filter does.

### What a P4 delivery success does and does not prove

One message reached a Gmail Primary Inbox with SPF, DKIM and DMARC all passing.
That proves the authentication chain is correctly configured end to end. It does
**not** predict future inbox placement: reputation is built over time and
volume, and a single message from a domain with no history is the easiest case
there is.

---

## 8. Free accounts

Not built, and not buildable from anything in P5. See DEC-018 for the full
policy design and the classified reserved-username list, and DEC-015 for the
placement at P7.5.

The one thing worth repeating here: **external forwarding must not ship enabled
on a free account.** It converts a free mailbox into an untraceable relay with
MateMail's reputation attached, and it is the single capability that makes
automated free signups worth an attacker's trouble.

---

## 9. Where the code is

| Concern | Module |
|---|---|
| The capability gate | `apps/tenants/policy.py` |
| Approval, suspension, abuse actions | `apps/platform_admin/approval.py` |
| Platform admin endpoints | `apps/platform_admin/views.py` |
| SMTP policy bridge | `apps/smtp_policy/views.py` |
| Sending limits | `apps/smtp_policy/rate_limits.py` |
| Plan limits | `apps/billing/models.py`, `apps/billing/utils.py` |
| Ownership verification | `apps/domains/verification.py` |
| Ownership re-verification sweep | `apps/domains/tasks.py` |
| Engine-side suspension | `apps/mail_engine/tasks.py` |
| Platform sender | `apps/accounts/mailer.py`, `PLATFORM_SENDER_ADDRESSES` |
| Postfix policy bridge | `scripts/postfix_policy_bridge.py` |
| Postfix restriction override | `deploy/engine/postfix-extra.cf` |
| Bridge sidecar | `deploy/engine/docker-compose.override.yml` |
| Engine install | `scripts/install-policy-bridge.sh` |

Tests, in two deliberately separate evidence classes.

**Application policy** — `tests/test_tenant_approval.py`,
`tests/test_smtp_policy_bridge.py`, `tests/test_sending_limits.py`,
`tests/test_ownership_reverification.py`, `tests/test_p5_migrations.py`,
`tests/test_policy_bridge.py`.

**Engine integration** — `tests/test_engine_policy_integration.py`
(configuration and restriction ordering) and
`tests/test_policy_protocol_integration.py` (the real bridge, the real
protocol, a live MateMail).

The split matters: a Python test cannot demonstrate Postfix behaviour, and
one that claimed to would be the most dangerous kind of green. What remains
unproven until deployment is that Postfix invokes the hook at the stages
configured — which is why the installer ends by printing the engine's own
`postconf` output.
