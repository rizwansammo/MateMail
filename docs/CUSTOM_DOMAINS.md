# MateMail Custom Hub/PostBox Domains

**Status:** Production live; NetaMate acceptance and legacy retirement complete  
**Date:** 2026-10-06

## Production acceptance — 2026-10-06

The first production tenant is live on the normal custom-host path:

- Hub: `mailhub.netamate.com CNAME custom.matemail.online`
- PostBox: `postbox.netamate.com CNAME custom.matemail.online`
- both rows are DNS verified, HTTPS active and routing active;
- both generated vhosts use the canonical MateMail frontend on `127.0.0.1:3020`;
- legacy `DEDICATED_TENANT_HOSTS`, fixed-host entries, the port-3060 frontend,
  legacy NetaMate nginx vhosts and the `/opt/NetaMate-Email` deployment were retired;
- the obsolete `mailadmin.netamate.com` certificate lineage was revoked and deleted.

The Phase 1 section below is retained as historical audit evidence; statements
there describe the pre-migration production state, not the current topology.

## Goal

Allow an organization administrator to attach customer-owned hostnames to
MateMail with a minimal setup:

```
mail.customer.example  CNAME  custom.matemail.online
hub.customer.example   CNAME  custom.matemail.online
```

The browser must remain on the customer hostname. A custom hostname is an
alternate HTTPS entry point to the existing MateMail Hub or PostBox surface; it
is **not** an HTTP redirect to `portal.matemail.online` or
`postbox.matemail.online`.

This feature changes only web access. It does not alter MX, SPF, DKIM, DMARC,
Postfix, Dovecot, Rspamd, mailbox storage or outbound reputation.

## Phase 1 production audit

The live MateServer was inspected read-only before choosing the edge design.

Observed production state:

- host-native nginx 1.28.3 owns public TCP 80 and 443;
- the same nginx instance serves MateMail and the other production applications
  on MateServer;
- MateMail application upstreams are loopback-only:
  - Django: `127.0.0.1:8020`
  - Next.js: `127.0.0.1:3020`
- the NetaMate dedicated frontend is currently `127.0.0.1:3060`;
- Certbot 4.0.0 is installed and `certbot.timer` is enabled;
- the MateMail, NetaMate Hub and NetaMate PostBox certificates all use the
  existing `webroot` flow at `/var/www/html`;
- `custom.matemail.online` resolves to the MateServer public IP;
- Caddy is not installed;
- NetaMate already proves the routing model manually:
  `mailadmin.netamate.com` and `postbox.netamate.com` terminate TLS in
  host nginx and proxy the original Host to the shared MateMail backend.

The application already has useful foundations:

- Host-bound tenant isolation exists through `DEDICATED_TENANT_HOSTS`;
- dedicated-host login, workspace scoping, API-key scoping and PostBox mailbox
  access already enforce the bound tenant;
- Hub browser API calls are same-origin in production;
- PostBox API calls are same-origin;
- the Hub refresh cookie has no Domain attribute and is host-only;
- PostBox uses a `__Host-` host-only session cookie;
- nginx forwards the original `Host` header to Django.

## Edge decision: keep nginx + Certbot

Caddy is **not** introduced for this feature.

Putting Caddy in front of MateMail would require it to own 80/443. Those ports
are already the shared entry point for many unrelated production applications.
Moving them behind a new proxy would turn a MateMail feature into a host-wide
edge migration.

Running Caddy behind nginx does not solve the certificate problem cleanly:
the component terminating public TLS still needs the customer certificate.

For the expected scale (tens to hundreds of custom hostnames), the existing
nginx + Certbot stack is simpler, already proven on this server, and avoids
changing unrelated applications.

The custom-domain subsystem must nevertheless remain **edge-independent**:
application state is only hostname -> organization -> surface. A future proxy
migration must not require changing tenant/domain data.

## Final request path

### Hub

```
hub.customer.example
        |
        | DNS CNAME
        v
custom.matemail.online -> MateServer
        |
        | TLS: nginx + Let's Encrypt
        v
nginx custom-host vhost
        |
        +-- /api/* -> 127.0.0.1:8020
        |
        +-- /*     -> 127.0.0.1:3020
                       |
                       v
                 MateMail Hub
```

### PostBox

```
mail.customer.example
        |
        | DNS CNAME
        v
custom.matemail.online -> MateServer
        |
        | TLS: nginx + Let's Encrypt
        v
nginx custom-host vhost
        |
        +-- /api/* -> 127.0.0.1:8020
        |
        +-- /*     -> 127.0.0.1:3020
                       |
                       | trusted edge header:
                       | X-MateMail-Surface: postbox
                       v
                    PostBox
```

No redirect to a MateMail hostname occurs.

## Source of truth

Phase 2 will replace deployment-only custom-host mapping with application data.

Each custom hostname has, at minimum:

- organization / tenant;
- normalized ASCII hostname;
- surface: `hub` or `postbox`;
- DNS status;
- provisioning status;
- certificate status;
- timestamps and last error suitable for the organization administrator.

A hostname is globally unique. One hostname cannot belong to two organizations
or to both surfaces.

The first version permits one active custom Hub hostname and one active custom
PostBox hostname per organization. This keeps the customer UI and certificate
lifecycle unambiguous; aliases can be added later without changing the model.

`DEDICATED_TENANT_HOSTS` remains as a compatibility fallback until the current
NetaMate bindings are imported and the migration is proven.

## Host validation

Arbitrary customer hostnames cannot be enumerated safely in Django's static
`ALLOWED_HOSTS` environment variable, and restarting the backend for every
customer hostname is rejected.

Phase 2/4 will therefore add an application host-allowlist middleware backed by
the custom-host table, with a short cache.

Production Django may accept syntactically valid Host headers at the framework
setting level, but the new middleware must reject every host that is neither:

1. a fixed MateMail/internal hostname explicitly configured by us; nor
2. an ACTIVE custom hostname in the database.

The custom-host lookup is a security boundary, not merely routing convenience.
Unknown hosts fail before tenant/auth business logic runs.

Customer-controlled input is never written directly into nginx configuration.
The hostname is normalized (including IDNA/punycode where supported) and
strictly validated before it can enter provisioning state.

## DNS verification

Customer-facing setup is intentionally one record.

Example:

```
Type:   CNAME
Host:   mail
Target: custom.matemail.online
```

MateMail verifies that the submitted hostname resolves through the required
CNAME target before marking it eligible for provisioning.

DNS ownership is proved by control of that hostname. This is separate from a
customer's **mail domain** verification and does not replace any MX/SPF/DKIM/
DMARC requirement.

## Privilege boundary

The Django container must never receive:

- root on MateServer;
- the Docker socket;
- write access to `/etc/nginx`;
- write access to `/etc/letsencrypt`;
- permission to execute arbitrary host commands.

Certificate/nginx work therefore runs in a small root-owned **host provisioning
worker** installed by Phase 3.

The worker consumes only already-approved provisioning jobs from a dedicated
loopback/internal API protected by a purpose-specific secret. It reports
success/failure back to the application.

The worker is allowed to perform only the fixed sequence required for a
validated hostname:

1. install an exact-host HTTP bootstrap vhost;
2. `nginx -t`;
3. reload nginx;
4. request one Let's Encrypt certificate with Certbot webroot;
5. replace the bootstrap with the final HTTPS vhost;
6. `nginx -t`;
7. reload nginx;
8. report status.

No shell fragment supplied by a customer is ever executed.

A short systemd timer/poller is preferred over granting the web application
host privileges. A provisioning delay measured in seconds/minutes is acceptable
for a DNS/SSL setup flow.

## Certificate lifecycle

Each custom hostname receives its own certificate lineage. This avoids coupling
two independently changeable customer hostnames into one certificate.

The final HTTP vhost keeps:

```
location /.well-known/acme-challenge/ {
    root /var/www/html;
}
```

so Certbot's existing webroot renewal mechanism remains valid.

Phase 3 adds a certificate-scoped deploy hook for MateMail custom certificate
lineages. On a successful custom-certificate renewal it runs:

1. `nginx -t`;
2. reload nginx only if the test passes.

Renewal of an unrelated certificate must not trigger custom-domain state
changes.

## Frontend routing

Canonical PostBox is currently identified by its known hostname. That is not
enough for arbitrary names such as `inbox.customer.example`.

Generated PostBox vhosts will overwrite, not trust, a private routing header:

```
X-MateMail-Surface: postbox
```

Next.js middleware will treat that header as authoritative only because
host-native nginx sets it on the upstream request. A browser-supplied value is
overwritten at the edge.

Hub is the default customer console surface but will use the same explicit
model where doing so improves clarity.

The backend does **not** use this header as the tenant authorization source.
Hostname -> tenant/surface database state remains authoritative.

## Authentication and cookies

The current cookie design is compatible with custom hostnames and should remain
host-scoped.

- Hub refresh cookie: host-only, HttpOnly, Secure, SameSite=Strict.
- PostBox session: `__Host-`, host-only, HttpOnly, Secure.

A login on `hub.customer.example` therefore creates a session for that host,
not a cookie shared with `portal.matemail.online`. That is desirable isolation.

Production browser API traffic stays same-origin, so a customer hostname does
not need to be appended to a giant static CORS origin list.

## Custom Hub edge restrictions

A tenant Hub custom hostname must not expose operator/internal surfaces merely
because it reaches the shared backend.

The generated vhost blocks at the edge, and the application independently
enforces, at least:

- `/api/internal/`
- `/api/platform/`
- `/platform`
- `/admin`
- `/django-admin`
- public organization signup on a tenant-bound hostname

## Custom PostBox edge restrictions

The generated PostBox vhost blocks operator surfaces and routes only the shared
PostBox frontend/API behavior. It keeps the current larger request/time limits
needed for attachments and mail operations.

PostBox tenant isolation is re-checked by the backend from the hostname mapping;
a mailbox from another organization must receive the same refusal as it does on
the current NetaMate dedicated hostname.

## Failure and removal behavior

A custom hostname moves through explicit states rather than becoming live on
submit.

Implemented backend lifecycle:

```
DNS: PENDING -> VERIFIED / FAILED

Edge:
UNPROVISIONED
    -> PROVISIONING
    -> READY
    -> ACTIVE

Provisioning failure
    -> ERROR -> PROVISIONING

Removal
    -> DEACTIVATING
    -> INACTIVE
```

`READY` is deliberate: nginx/TLS exists, but the application still refuses
the hostname. Phase 4's internal activation queue then installs the final
surface-aware vhost and only afterwards advances the row to `ACTIVE`.
A crash between those two operations fails closed: the Host guard still rejects
the hostname and the READY queue retries the idempotent activation on its next
poll.

Removing a hostname revokes the application mapping first, then removes the
edge vhost/certificate material. A stale certificate must never imply that an
old tenant mapping is still authorized.

If DNS later moves away, the hostname may be marked unhealthy. Existing mail
delivery is unaffected because this feature is web-only.

## NetaMate pilot

`mailadmin.netamate.com` and `postbox.netamate.com` are the first production
pilot because they already exercise:

- customer-owned DNS;
- individual Let's Encrypt certificates;
- host-bound tenant isolation;
- shared MateMail backend.

Phase 5 must preserve the current NetaMate frontend behavior during the pilot.
The existing dedicated frontend on `127.0.0.1:3060` is not removed as an
incidental part of custom-domain provisioning. Consolidating that frontend is a
separate decision.

## Revised implementation phases

1. ✅ **Audit + Final Architecture** — complete. No production mutation.
2. ✅ **Custom Domain Backend** — complete: model, authorization, DNS
   verification, dynamic host allowlisting and provisioning API/state.
3. **nginx + Certbot Automation** — ✅ complete: root-owned host worker,
   exact-host ACME bootstrap, individual Certbot lineage, TLS-ready staging
   vhost, scoped renewal hook, systemd timer and exact-release deployment
   artifacts. **No Caddy migration.**
4. ✅ **Routing + Authentication** — complete: ACTIVE database bindings now
   drive tenant resolution, exact-host nginx routing and frontend surface
   selection; Hub/PostBox auth and cookies remain host-isolated.
5. 🟡 **Hub UI + NetaMate Pilot + Production Tests** — Hub UI, safe removal,
   production smoke tooling and the guarded NetaMate adoption path are complete.
   The live NetaMate proof is intentionally deferred until the owner deploys the
   final merged release.
6. **Hub canonical rename** — `portal.matemail.online` ->
   `hub.matemail.online`, with the old hostname retained as a permanent
   redirect until access logs justify removal.

## Phase 1 acceptance result

PASS:

- current 80/443 ownership verified;
- current nginx upstream/routing verified;
- current Certbot method and timer verified;
- existing NetaMate custom-host pattern verified;
- `custom.matemail.online` DNS verified;
- cookie/auth/tenant host behavior audited in source;
- edge technology chosen without changing production.

Phase 1 deliberately changes no live server configuration.

## Phase 3 implementation result

Phase 3 is implemented but remains undeployed on this feature branch.

The release now contains `deploy/custom-hosts/`:

- a standard-library Python provisioner that polls only the loopback internal
  API and never executes customer input as shell;
- strict duplicate-run locking under `/run/lock`;
- an exact-host port-80 bootstrap that serves only the ACME webroot and 503s
  everything else;
- one Let's Encrypt lineage per customer hostname using the existing Certbot
  webroot;
- certificate hostname/expiry validation before nginx is allowed to reference
  the lineage;
- an atomic nginx writer that refuses to overwrite operator-owned files and
  restores the previous generated config if `nginx -t` fails;
- a Phase-3 TLS-ready staging vhost that deliberately returns 503 rather than
  proxying MateMail before Phase 4;
- a certificate-scoped Certbot deploy hook that reloads nginx only for generated
  MateMail custom-host sites and only after `nginx -t`;
- a hardened root-owned systemd oneshot + timer;
- an idempotent installer that establishes a separate
  `CUSTOM_HOST_PROVISIONER_SECRET` without printing it;
- manual deployment workflow integration that stages the worker from the exact
  release SHA, installs it before the backend starts, then enables the timer
  only after the backend is healthy.

Crash recovery is deliberate: a row left in `PROVISIONING` by a killed worker
is returned to the next poll and the provisioning sequence is idempotently
resumed. A deliberate `ERROR` is not retried forever (which could hit CA rate
limits); a fresh successful DNS Verify explicitly requeues it.

Phase 3 originally stopped at `READY`. Phase 4 deliberately extended the
same narrow worker contract with a separate activation queue; the final
`ACTIVE` transition occurs only after the generated surface-aware nginx vhost
has passed validation and reloaded successfully.


## Phase 2 acceptance result

PASS:

- `CustomHostname` is durable tenant-owned application state, with separate
  Hub/PostBox surfaces, DNS state, edge state and certificate state;
- the database enforces global live-hostname uniqueness and one live hostname
  per organization/surface, including concurrent requests;
- customer hostnames are normalized, IDNA-safe and reject URLs, ports,
  wildcards, IP literals, MateMail-owned names and reserved fixed names;
- the customer setup contract is one direct CNAME to
  `custom.matemail.online`;
- DNS verification is exact, retryable and rate-limited to 10 checks/hour per
  custom-host record;
- only authenticated owner/admin roles may add, verify or remove a hostname;
  read-only members cannot mutate, and cross-tenant detail access returns 404;
- add/verify fail closed for organizations that are not eligible to use the
  service, while an authorized admin may still relinquish an unprovisioned
  hostname after suspension;
- the Phase 3 host-worker API uses its own purpose-specific secret, exposes only
  verified/eligible mappings, and cannot mark a hostname ACTIVE;
- the dynamic Host guard exists now but activation remains OFF by default.
  When enabled later, only fixed operator-owned hosts or database rows in
  `ACTIVE` state pass; `READY` still fails closed;
- Django system checks refuse an unsafe dynamic-host deployment if the guard is
  missing/reordered or the Host settings are inconsistent;
- Django receives no nginx, Certbot, Docker-socket or root privilege.

### Phase 2 validation

The branch passed the repository's complete quality gates after implementation:

- `manage.py check` — PASS;
- `manage.py check --deploy` — PASS;
- `makemigrations --check --dry-run` — PASS;
- full Django test suite, including the custom-host security regressions — PASS;
- pytest-only regression suite — PASS;
- production Compose validation and NetaMate infrastructure invariants — PASS;
- frontend ESLint — PASS;
- canonical MateMail production build — PASS;
- NetaMate Email production build — PASS.

No production nginx, certificate, database or application deployment was
changed by Phase 2. `CUSTOM_HOSTS_DYNAMIC_ENABLED` remains `False` until
Phase 4.


## Phase 4 implementation result

Phase 4 is implemented on the feature branch and remains undeployed.

### Request and tenant binding

Dynamic customer-host acceptance is now enabled in the production release
configuration. Django's static `ALLOWED_HOSTS` opens only so the first
middleware can perform the real allowlist decision; an unknown hostname still
fails closed.

For an accepted custom hostname, that exact ACTIVE database binding is attached
to the request and reused by downstream tenant/surface resolution. The request
therefore has one authoritative decision rather than separate Host checks that
could disagree.

The existing dedicated-host authorization mechanisms now work for dynamic
custom hostnames too:

- Hub login selects only membership in the hostname-bound organization;
- workspace listing/switching cannot leave that organization;
- JWT refresh rejects a token whose `tenant_id` belongs to another
  organization;
- API keys for another organization are rejected;
- PostBox login, retained-account switching and every authenticated mailbox
  request re-check the mailbox's organization against the hostname binding.

### Surface isolation

A custom hostname belongs to exactly one customer surface.

For a Hub hostname, nginx and application middleware deny internal/platform,
Django-admin and PostBox routes. For a PostBox hostname, nginx exposes only the
PostBox API (plus health) and the application middleware independently refuses
Workspace authentication/administration APIs.

This is deliberate defense in depth: a future nginx edit must not turn a
customer hostname into an alternate Platform or cross-surface address.

### Browser routing

The final generated nginx vhost preserves the customer Host header and proxies
same-origin to the existing loopback services. There is no redirect to
`portal.matemail.online` or `postbox.matemail.online`.

Arbitrary PostBox names cannot be identified by a compiled hostname list, so the
generated exact-host vhost overwrites:

```
X-MateMail-Custom-Host: 1
X-MateMail-Surface: postbox
```

The Next.js middleware uses these only for frontend presentation/routing.
Configured canonical hosts ignore them even if a browser supplies them. Backend
tenant authorization never trusts these headers; it resolves the Host from the
database.

### Cookies, CORS and CSRF

Browser APIs remain relative/same-origin on every custom hostname, so no
customer domains are appended to a growing static CORS origin list.

Authentication credentials remain host-scoped:

- Hub refresh: HttpOnly, host-only, Secure in production,
  `SameSite=Strict`, path `/api/auth/`;
- PostBox active and saved-account sessions: `__Host-` cookies, host-only,
  Secure, HttpOnly and path `/`.

Therefore a session created on one customer's hostname is not sent to canonical
MateMail or another customer's hostname.

The Hub API still authenticates ordinary requests with the bearer access token;
the refresh cookie is consumed only by the same-origin refresh endpoint.
PostBox has its separate mailbox-session authentication. No wildcard CSRF/CORS
trust is introduced.

Account recovery, email verification and team-invite email URLs intentionally
remain on the canonical MateMail Hub rather than being built from an incoming
Host header. That prevents Host-header poisoning and keeps a recovery link
usable even after a customer changes or removes its custom hostname.

### HSTS on customer-owned names

Django's canonical production policy carries `includeSubDomains`, which is
appropriate for MateMail-owned domains but not for a customer-owned hostname.
Generated custom vhosts therefore hide any upstream HSTS header and emit only:

```
Strict-Transport-Security: max-age=31536000
```

MateMail never asserts HSTS policy over subdomains it does not own.

### Activation ordering

The worker has two durable queues:

1. DNS-verified hostname -> certificate provisioning -> `READY`;
2. `READY` + active certificate -> final surface vhost -> `ACTIVE`.

The state update to ACTIVE happens only after `nginx -t`, nginx reload,
certificate validation and a fresh backend authorization check. The application
cache is invalidated on transition so the new binding becomes request-eligible
without a process restart.


## Phase 5 implementation result

The customer-facing implementation is complete on the feature branch. No
production hostname has been migrated because the owner explicitly requires all
custom-domain phases to be completed before merging and deploying.

### Hub customer experience

Workspace Settings now contains a **Custom access URLs** area with separate
MateMail Hub and PostBox panels. Owners/admins can:

1. enter any valid customer-owned hostname;
2. copy the exact CNAME instruction to `custom.matemail.online`;
3. run DNS verification;
4. watch DNS, HTTPS and edge activation status;
5. open an ACTIVE custom URL without any canonical-host redirect;
6. remove a hostname through the durable deactivation lifecycle.

Transient provisioning/removal states are polled automatically. Read-only
workspace roles can inspect the configuration but cannot mutate it.

### Safe customer removal

Removal is no longer blocked once edge provisioning has started.

A customer DELETE first moves the database row to `DEACTIVATING`. Because
only ACTIVE rows are request-eligible, access is revoked immediately. The
root-owned worker then:

1. verifies it owns the generated nginx file;
2. disables the generated enabled symlink;
3. runs `nginx -t` and reloads;
4. asks Certbot to revoke/delete the exact per-host lineage;
5. reports `INACTIVE / REVOKED`.

Cleanup is allowed even when the tenant has since been suspended or DNS has
moved away. Operator-owned nginx files are never deleted.

### NetaMate pilot preparation

The live audit confirmed the two intended pilot hostnames currently use
hand-written nginx vhosts, existing certificates and the NetaMate-branded
frontend on `127.0.0.1:3060`.

The generic worker therefore gained a **root-owned, exact-host frontend
override** for controlled adoption. It accepts only loopback HTTP high ports
and is never customer-controlled. This preserves NetaMate branding while the
same database/edge lifecycle is exercised.

A guarded management command,
`adopt_dedicated_custom_hostname`, can stage an existing
`DEDICATED_TENANT_HOSTS` name at READY only when:

- the deployment binding already maps it to the requested tenant;
- the tenant is eligible;
- the hostname now satisfies the same direct CNAME contract as every customer;
- database uniqueness permits the mapping.

The root worker still verifies the existing certificate and installs the final
generated vhost before ACTIVE.

The exact post-deploy handover, rollback and evidence checklist lives in
`docs/CUSTOM_DOMAIN_NETAMATE_PILOT.md`.

### Production smoke tooling

`deploy/custom-hosts/smoke_test.py` is a read-only post-deploy test. It checks:

- direct CNAME;
- trusted hostname-valid TLS;
- no canonical MateMail redirect leakage;
- customer-safe HSTS;
- Hub isolation from internal/Platform/PostBox/signup routes;
- PostBox isolation from internal/Platform/Workspace routes.

It does not log in or change application, DNS, nginx or certificate state.

### Phase 5 completion gate

Phase 5 engineering is **complete**. The customer UI, lifecycle, removal,
NetaMate adoption path, guarded branded-frontend override, non-destructive smoke
tool, rollback runbook and read-only production baseline are all finished.

The remaining NetaMate check is a **post-deploy acceptance gate**, not missing
implementation: after the final release is deployed by the owner,
`mailadmin.netamate.com` and `postbox.netamate.com` must pass the automated
smoke test plus the manual authenticated/branding checks in the pilot runbook.

That deferral is intentional; performing the live handover now would contradict
the agreed rule that this branch is not deployed until all phases are finished.
