# MateMail custom-host edge worker

This directory implements the Phase 3 TLS provisioner, Phase 4 activation
worker, and Phase 5 deactivation/pilot support for custom Hub/PostBox hostnames.

The Django application never receives host privileges. A root-owned systemd
oneshot polls the loopback backend API, re-authorizes each DNS-verified hostname,
installs an exact-host nginx bootstrap, obtains a Let's Encrypt certificate with
the server's existing Certbot webroot, and replaces the bootstrap with a
TLS-ready staging vhost.

The staging vhost intentionally returns HTTP 503. After the application reports
the hostname READY, the same worker consumes the Phase 4 activation queue,
installs the final surface-aware Hub/PostBox proxy vhost, validates nginx, and
only then reports the hostname ACTIVE.

## Runtime paths

- worker: `/usr/local/libexec/matemail-custom-host-provisioner`
- worker env: `/etc/matemail/custom-host-provisioner.env` (0600)
- generated sites: `/etc/nginx/sites-available/matemail-custom-<hostname>.conf`
- enabled symlinks: `/etc/nginx/sites-enabled/matemail-custom-<hostname>.conf`
- Certbot hook: `/etc/letsencrypt/renewal-hooks/deploy/matemail-custom-host-nginx.sh`
- systemd: `matemail-custom-host-provisioner.timer`

The shared secret is purpose-specific. It must equal
`CUSTOM_HOST_PROVISIONER_SECRET` in `/opt/MateMail/app/.env` and is copied into
the worker's root-only environment file by `install.sh`. It is never printed.

## Removal

Customer removal moves an edge-backed row to DEACTIVATING immediately, so the
application Host guard stops accepting it. The worker then removes only its
marked generated nginx site, reloads after `nginx -t`, retires the per-host
Certbot lineage, and reports INACTIVE/REVOKED. Cleanup retries even if the tenant
has since been suspended or DNS has moved.

## Failure behavior

A broken candidate nginx config is restored before the worker returns. Certbot
failure leaves the ACME bootstrap in place, so a later explicit DNS Verify can
requeue the hostname without rebuilding the edge manually. A certificate failure leaves the row in ERROR and requires a fresh successful
DNS Verify before another ACME attempt. A crash after certificate issuance
leaves the row READY; activation retries idempotently without reissuing the
certificate. The final database transition to ACTIVE occurs only after the
surface-aware nginx vhost has passed `nginx -t` and reloaded successfully.

## Production identity and privileged validation (2026-10-09)

The privileged host worker independently rejects `matemail.pro` and **all**
`*.matemail.pro` hostnames, alongside the retired `matemail.online` suffix.
This mirrors the backend reserved-suffix policy and prevents an invalid job
from changing the platform's own Nginx site or obtaining a certificate for it.
The worker also refuses IP literals and malformed/non-public TLDs.

The canonical customer CNAME target is `custom.matemail.pro`. The standalone
read-only smoke test defaults to this target and rejects redirects that move a
customer to **any** other hostname. Older `.online` CNAME acceptance in the
Django service is an explicit temporary legacy compatibility setting, not the
canonical configuration for a new customer.

Rollout is separate from the application image deployment: after GitHub CI,
back up the installed root worker, verify its exact revision and install the
validated worker atomically; keep its existing root-only environment and the
active timer intact. A read-only function-level validation and an empty-queue
systemd poll can verify it **without adding any customer domain or issuing an
unnecessary certificate**. The full DNS + Let's Encrypt + tenant lifecycle
acceptance still needs an actual customer-owned hostname later.
