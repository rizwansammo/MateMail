# MateMail custom-host edge worker

This directory is Phase 3 of the custom Hub/PostBox hostname project.

The Django application never receives host privileges. A root-owned systemd
oneshot polls the loopback backend API, re-authorizes each DNS-verified hostname,
installs an exact-host nginx bootstrap, obtains a Let's Encrypt certificate with
the server's existing Certbot webroot, and replaces the bootstrap with a
TLS-ready staging vhost.

The staging vhost intentionally returns HTTP 503. Phase 4 replaces it with the
Hub/PostBox proxy only after hostname-aware routing/authentication is complete.

## Runtime paths

- worker: `/usr/local/libexec/matemail-custom-host-provisioner`
- worker env: `/etc/matemail/custom-host-provisioner.env` (0600)
- generated sites: `/etc/nginx/sites-available/matemail-custom-<hostname>.conf`
- enabled symlinks: `/etc/nginx/sites-enabled/matemail-custom-<hostname>.conf`
- Certbot hook: `/etc/letsencrypt/renewal-hooks/deploy/matemail-custom-host-nginx.sh`
- systemd: `matemail-custom-host-provisioner.timer`

The shared secret is purpose-specific. It must equal
`CUSTOM_HOST_PROVISIONER_SECRET` in `/opt/MateMail/.env` and is copied into
the worker's root-only environment file by `install.sh`. It is never printed.

## Failure behavior

A broken candidate nginx config is restored before the worker returns. Certbot
failure leaves the ACME bootstrap in place, so a later explicit DNS Verify can
requeue the hostname without rebuilding the edge manually. The backend never
allows the Phase 3 worker to mark a hostname ACTIVE; READY is the highest state
it can report.
