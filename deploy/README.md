# MateMail deployment

Everything needed to run MateMail on **MateServer**. The control plane and the
Mail Engine are both deployed; what follows is how they are configured.

> **Production deployments are intentional and SHA-pinned.**
> Merging to `main` publishes release images but does not touch MateServer.
> Use the manual **Deploy to MateServer** workflow with the exact green commit
> SHA. The workflow validates topology, creates a pre-deploy database dump,
> deploys the matching Compose revision and rolls back on failed health checks.

---

## Contents

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Production stack. Copied to `/opt/MateMail/docker-compose.yml`. |
| `env.production.example` | Template for `/opt/MateMail/.env`. Placeholders only. |
| `nginx/portal.matemail.online.conf` | Host-nginx vhost **template** for the MateMail Workspace, and the legacy `app.matemail.online` redirect. Declares the shared upstreams. Not installed by P2. |
| `nginx/postbox.matemail.online.conf` | Host-nginx vhost **template** for MateMail PostBox. |
| `nginx/platform.matemail.online.conf` | Host-nginx vhost **template** for the Platform Console. |
| `nginx/autodiscover.matemail.online.conf` | Host-nginx vhost **template** for the Outlook Autodiscover endpoint. Allow-list of five exact paths; 404 otherwise. |
| `nginx/*.bootstrap.conf` | HTTP-only vhosts used **only** to obtain each certificate. No TLS directive and no upstream, so they parse on a host with neither. Replaced by the real vhost immediately afterwards — see docs/DEPLOYMENT.md § Certificates and the bootstrap order. |

---

## Architecture

```
                     Internet
                        │ 443
              ┌─────────▼──────────┐
              │ host-native nginx  │  MateServer's existing nginx + certbot.
              │ (the only public   │  MateMail adds no second nginx.
              │  reverse proxy)    │
              └────┬──────────┬────┘
        /api/ →    │          │    → everything else
                   │          │
    127.0.0.1:8020 │          │ 127.0.0.1:3020
              ┌────▼───┐  ┌───▼──────┐
              │backend │  │ frontend │   GHCR images, SHA-pinned
              └────┬───┘  └──────────┘
                   │  internal network only (no host ports)
        ┌──────────┼───────────┬──────────────┐
   ┌────▼────┐ ┌───▼───┐ ┌─────▼──────┐ ┌─────▼─────┐
   │postgres │ │ redis │ │celery-work.│ │celery-beat│
   └─────────┘ └───────┘ └────────────┘ └───────────┘
                          (backend image, different command)
```

**Port allocation.** Backend `127.0.0.1:8020`, frontend `127.0.0.1:3020`.
`8015`, `8016` and `3015` belong to **MateConnect** and must never be used here.
PostgreSQL and Redis publish **no host port** at all.

**The Mail Engine remains a separate Compose project.** The control-plane
backend and Celery worker reach it only through the externally managed,
Docker-internal `matemail_engine_link` network. The production deployment
verifies that this network already exists with `internal=true`; it never creates
or weakens that boundary during an application deploy.

---

## Images

```
ghcr.io/rizwansammo/matemail-backend:<commit-sha>
ghcr.io/rizwansammo/matemail-frontend:<commit-sha>
```

Built by CI, never on the server. Selected by two variables in `/opt/MateMail/.env`:

```
MATEMAIL_BACKEND_IMAGE
MATEMAIL_FRONTEND_IMAGE
```

Both are declared `:?` in compose, so a missing value fails the command rather
than starting something unintended.

**There is no `latest` tag for production.** A rollback has to be able to name
the exact image it is returning to, and `latest` cannot express that. CI also
pushes a `:main` tag purely for browsing GHCR — do not deploy it.

`NEXT_PUBLIC_*` values are compiled into the frontend bundle at **image build
time**. Changing a public URL therefore needs a rebuild, not a restart.

---

## First-time host bootstrap

Run this only when bootstrapping MateMail on a new production host. Existing MateServer deployments should use the release workflow below.

```bash
sudo mkdir -p /opt/MateMail/backups
sudo chown "$USER":"$USER" /opt/MateMail /opt/MateMail/backups

# 1. Compose file (copied from the repo on a workstation, or fetched by hand).
#    The server needs no git checkout — the image is the artifact.
scp deploy/docker-compose.yml mateserver:/opt/MateMail/docker-compose.yml

# 2. Environment. Create it in place with 0600 and fill it in on the server;
#    generate secrets there, never on a laptop.
install -m 0600 /dev/null /opt/MateMail/.env
#    then paste the contents of deploy/env.production.example and complete it
#    python3 -c "import secrets; print(secrets.token_urlsafe(50))"

# 3. GHCR pull authentication, per the NetaMate production model.
#    Uses the host's own docker login; the deploy workflow never ships a
#    registry credential to the server.
docker login ghcr.io -u <github-user>      # paste a read:packages PAT

# 4. Host nginx vhost (template — review before installing).
#
#    THREE steps, not one. nginx resolves `ssl_certificate` and the
#    certbot-provided `options-ssl-nginx.conf` when it PARSES its config,
#    not when a request arrives — so installing the TLS vhost before certbot
#    has run makes `nginx -t` fail for the WHOLE server, and on a fresh
#    host nginx will not start at all. certbot cannot go first either:
#    HTTP-01 needs something already answering on port 80.
#
#    The bootstrap vhost is HTTP-only and exists to break that cycle. Full
#    reasoning: docs/DEPLOYMENT.md § Certificates and the bootstrap order.

#    4a. Bootstrap vhost — no TLS directives, parses with no certificate.
sudo cp deploy/nginx/portal.matemail.online.bootstrap.conf \
        /etc/nginx/sites-available/matemail
sudo ln -s /etc/nginx/sites-available/matemail /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

#    4b. One certificate, all four names. --webroot rather than --nginx: the
#        nginx plugin rewrites the server block it finds, and 4c replaces it.
sudo certbot certonly --webroot -w /var/www/html \
     -d portal.matemail.online -d matemail.online \
     -d www.matemail.online -d app.matemail.online

#    4c. Overwrite the SAME path with the real vhost. The symlink already
#        exists; enabling a second file instead would leave two :80 blocks
#        for one server_name and a conflicting-server-name warning.
sudo cp deploy/nginx/portal.matemail.online.conf \
        /etc/nginx/sites-available/matemail
sudo nginx -t && sudo systemctl reload nginx

# 5. The private Mail Engine link. REQUIRED — backend and celery-worker join it,
#    and the deploy refuses to run without it.
#
#    --internal is not optional. Without it Docker creates a routable bridge,
#    and a routable engine link is reachable from every other Docker network on
#    this host, which is the exposure the dedicated network exists to remove.
#
#    Created by hand, once, and owned outside both Compose projects so that
#    neither MateMail's nor the engine's `docker compose down` can delete a
#    network the other still depends on. The deploy VERIFIES it and never
#    creates one — see deploy/engine/README.md.
docker network create --internal matemail_engine_link
docker network inspect matemail_engine_link --format '{{.Name}} internal={{.Internal}}'
```

The Mail Engine's own gateway configuration is versioned separately in
[`deploy/engine/`](engine/README.md), which covers installing it, validating the
private path, and the checks required before every engine upgrade.

Resulting layout — deliberately minimal:

```
/opt/MateMail/
├── docker-compose.yml          (installed by each deploy, from that release's commit)
├── .env                        (0600)
├── .env.bak.<timestamp>        (last 10, written by each deploy)
├── .deploy/                    (staged Compose candidates, last 5)
└── backups/
    ├── docker-compose.<timestamp>.yml   (last 10, written by each deploy)
    └── pre-deploy-<timestamp>.sql.gz    (database dumps)
```

Note that `docker-compose.yml` is **not** a file the host owns any more. Each
deployment installs the exact file from the commit being deployed, because the
Compose file now carries security-critical topology and must not drift from the
images it runs. The bootstrap copy above is only there to make the very first
deployment possible.

---

## Deploying

Always through the **Deploy to MateServer** GitHub Actions workflow, never by
hand. Three gates stand in front of it:

1. `workflow_dispatch` only — no push trigger exists.
2. `environment: production` — configure a required reviewer on that
   environment in repo settings to add a human approval step.
3. A `confirm` input that must be typed as exactly `MateServer`.

The workflow verifies both images exist in GHCR *before* touching the server,
takes a pre-deploy database dump, rewrites the two image variables in `.env`,
then `docker compose pull && docker compose up -d`, then waits for the backend
healthcheck.

Migrations run as a one-shot `migrate` service that must complete successfully
before backend, worker and beat start.

### Rolling back

```bash
cd /opt/MateMail
# Either restore the previous env file:
cp .env.bak.<timestamp> .env
# or set the two image variables to the previous SHA by hand, then:
docker compose up -d
```

A rollback is the same mechanism as a deployment, which is why SHA-pinned tags
matter: the previous `.env` names the exact images that were running.

---

## What P2 deliberately did not do

- Did not touch MateServer.
- Did not install or modify the real host nginx configuration.
- Did not deploy the Mail Engine.
- Did not enable automatic production deployment.
- Did not create placeholder mail infrastructure.
