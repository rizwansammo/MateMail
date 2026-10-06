# Phase 5 — NetaMate Custom-Hostname Production Pilot

**Status:** runbook ready; execute only after the custom-domain branch has been
merged and the matching release has been deployed to MateServer.

This pilot deliberately uses the existing NetaMate customer-facing names:

- Hub: `mailadmin.netamate.com`
- PostBox: `postbox.netamate.com`
- tenant: `netamate-solutions`

It does **not** replace the NetaMate branded frontend. The branded frontend
remains on `127.0.0.1:3060` while the generic custom-host edge lifecycle is
proven.

## Why this is an adoption, not a normal customer setup

These two names pre-date the custom-host feature. Production currently has:

- A records directly to MateServer rather than the new CNAME contract;
- individual Let's Encrypt certificates that are already valid;
- hand-written nginx vhosts;
- `DEDICATED_TENANT_HOSTS` bindings;
- the NetaMate frontend on `127.0.0.1:3060`.

A normal new customer has none of that state. Treating NetaMate as though it
were a new hostname would either discard branding or create an avoidable
certificate/vhost collision.

The adoption path therefore reuses the existing valid certificate, verifies
the same direct CNAME contract every future customer uses, moves the database
row to READY, and lets the root worker install the final generated vhost before
the row becomes ACTIVE.


## Pre-deploy production baseline — 2026-10-05

A read-only MateServer check was completed before any migration.

Current production facts:

- branded NetaMate frontend on `127.0.0.1:3060`: HTTP 200;
- `mailadmin.netamate.com`: A -> `169.58.114.252`, no CNAME yet;
- `postbox.netamate.com`: A -> `169.58.114.252`, no CNAME yet;
- both public roots return HTTP 200 with the existing trusted certificates;
- both emit customer-safe HSTS (`max-age=31536000`, no `includeSubDomains`);
- host nginx syntax is clean.

## Phase A repository decoupling

Before any DNS or nginx handover, MateMail CI/CD is decoupled from the legacy
NetaMate frontend deployment:

- MateMail CI no longer builds or publishes
  `ghcr.io/rizwansammo/matemail-frontend-netamate-email`;
- the MateMail deploy workflow no longer touches `/opt/NetaMate-Email`;
- the currently running branded frontend on `127.0.0.1:3060` is intentionally
  left running and unchanged as a rollback asset;
- the hand-written NetaMate nginx vhosts and their enabled symlinks remain
  untouched in this phase;
- no DNS record is changed in this phase.

This is a control-plane separation only. The later custom-host adoption still
uses the existing port-3060 frontend override until the pilot has passed.

Legacy-vhost isolation baseline:

| Host | Probe | Current result | Expected after custom-host handover |
|---|---|---:|---:|
| MailAdmin | `/api/internal/health/` | 404 | 404 |
| MailAdmin | `/api/platform/tenants/` | 404 | 404 |
| MailAdmin | `/api/postbox/auth/me/` | 403 | **404** |
| MailAdmin | `/signup` | 404 | 404 |
| PostBox | `/api/internal/health/` | 404 | 404 |
| PostBox | `/api/platform/tenants/` | 404 | 404 |
| PostBox | `/api/workspaces/` | 401 | **404** |

The two bold changes are intentional Phase 4 hardening: the generated exact-host
vhosts hide the wrong application surface at nginx before authentication runs.

This baseline is not the final pilot. The final smoke/manual proof below must be
run only after the owner deploys the merged release and the two A records have
been changed to the required CNAMEs.


## Pre-deploy configuration

Before the release that contains this feature is deployed, add this one
operator-only setting to `/opt/MateMail/.env`:

```dotenv
MATEMAIL_CUSTOM_HOST_FRONTEND_OVERRIDES=mailadmin.netamate.com=http://127.0.0.1:3060,postbox.netamate.com=http://127.0.0.1:3060
```

The worker accepts overrides only to `http://127.0.0.1:<high-port>`. This is
not a customer setting and must never appear in Hub.

Do not remove these compatibility values yet:

```dotenv
DEDICATED_TENANT_HOSTS=mailadmin.netamate.com=netamate-solutions,postbox.netamate.com=netamate-solutions
```

Keeping them through the pilot gives a conservative fallback while the dynamic
binding is being proven.

## DNS handover

After the matching release is deployed and healthy, replace the two existing
A records with direct CNAMEs:

```text
mailadmin.netamate.com  CNAME  custom.matemail.online
postbox.netamate.com    CNAME  custom.matemail.online
```

`custom.matemail.online` already resolves to the same MateServer. The TLS
identity remains each NetaMate hostname, so changing the DNS indirection does
not change what certificate the browser expects.

Verify before proceeding:

```bash
dig +short CNAME mailadmin.netamate.com
dig +short CNAME postbox.netamate.com
```

Both must return:

```text
custom.matemail.online.
```

## Stage the two existing names in MateMail

On MateServer:

```bash
cd /opt/MateMail

sudo docker compose exec -T backend python manage.py adopt_dedicated_custom_hostname \
  --tenant-slug netamate-solutions \
  --hostname mailadmin.netamate.com \
  --surface hub

sudo docker compose exec -T backend python manage.py adopt_dedicated_custom_hostname \
  --tenant-slug netamate-solutions \
  --hostname postbox.netamate.com \
  --surface postbox
```

The command is intentionally narrow:

- the hostname must already be mapped to the same tenant in
  `DEDICATED_TENANT_HOSTS`;
- the direct CNAME must verify;
- the tenant must be eligible;
- database uniqueness still applies;
- the result is READY, not ACTIVE.

The host worker then verifies the existing certificate and creates:

```text
/etc/nginx/sites-available/matemail-custom-mailadmin.netamate.com.conf
/etc/nginx/sites-available/matemail-custom-postbox.netamate.com.conf
```

Watch the bounded worker:

```bash
sudo systemctl start matemail-custom-host-provisioner.service
sudo journalctl -u matemail-custom-host-provisioner.service -n 120 --no-pager
```

Do not continue unless both mappings have reached ACTIVE and `sudo nginx -t`
passes.

## Hand over from the legacy vhosts

The old vhost source files remain on disk for rollback. Disable only their
enabled symlinks after the generated vhosts exist and the rows are ACTIVE:

```bash
sudo unlink /etc/nginx/sites-enabled/mailadmin.netamate.com.conf
sudo unlink /etc/nginx/sites-enabled/postbox.netamate.com.conf
sudo nginx -t
sudo systemctl reload nginx
```

If `nginx -t` fails, recreate the two symlinks immediately and do not reload:

```bash
sudo ln -s /etc/nginx/sites-available/mailadmin.netamate.com.conf \
  /etc/nginx/sites-enabled/mailadmin.netamate.com.conf
sudo ln -s /etc/nginx/sites-available/postbox.netamate.com.conf \
  /etc/nginx/sites-enabled/postbox.netamate.com.conf
sudo nginx -t
sudo systemctl reload nginx
```

## Automated production smoke test

From the matching repository checkout:

```bash
python3 deploy/custom-hosts/smoke_test.py \
  --hub-host mailadmin.netamate.com \
  --postbox-host postbox.netamate.com
```

Required PASS results include:

- direct CNAME to `custom.matemail.online`;
- trusted hostname-valid HTTPS;
- no redirect to a MateMail canonical hostname;
- customer-safe HSTS (no `includeSubDomains`);
- Hub blocks internal/Platform/PostBox/signup paths;
- PostBox blocks internal/Platform/Workspace paths.

## Manual browser proof

Automated HTTP checks cannot prove the UI branding or authenticated tenant
experience. Record these after the automated smoke test passes:

1. `https://mailadmin.netamate.com` keeps the NetaMate MailAdmin branding,
   signs into only `netamate-solutions`, and Hub pages/API work.
2. `https://postbox.netamate.com` keeps the NetaMate PostBox experience and
   a NetaMate mailbox can sign in/read mail.
3. A mailbox from another tenant is refused on the NetaMate PostBox hostname.
4. Browser address bar remains on the NetaMate hostname throughout.
5. No certificate warning, redirect loop, or canonical MateMail hostname leak.

Only after both automated and manual proof pass is the NetaMate pilot considered
complete.

## Pilot rollback rule

If a problem appears after the legacy symlinks were disabled, re-enable those
two legacy symlinks and reload nginx after `nginx -t`. Do **not** use the Hub
Remove button as the first rollback action: Remove deliberately retires the
custom-host certificate, while the legacy vhost also refers to that same
certificate lineage.

The custom-host database row may remain ACTIVE during a temporary edge rollback;
it is bound to the same NetaMate tenant and the Host guard prefers that safe
binding. Diagnose first, then decide whether the adoption should be undone.
