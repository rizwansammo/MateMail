# MateMail deployment

Everything needed to run MateMail on **MateServer**. Nothing here has been
applied to any server: P2 prepared the configuration, it did not deploy it.

> **MateMail is not ready for MateServer.**
> Per **DEC-012**, no real customer mail may reach the platform until P0–P7 are
> all complete. Still outstanding: the Mail Engine does not exist (P4), mail
> policy is not enforced (P5), backups are simulated (P6), and there is no
> operational monitoring (P7). The deploy workflow is manual-only for exactly
> this reason.

---

## Contents

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Production stack. Copied to `/opt/MateMail/docker-compose.yml`. |
| `env.production.example` | Template for `/opt/MateMail/.env`. Placeholders only. |
| `nginx/app.matemail.online.conf` | Host-nginx vhost **template**. Not installed by P2. |

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

**The Mail Engine is not in this stack.** It arrives in P4 as its own compose
project and will join via an additional external network declared then. The
previous `docker-compose.mailengine.yml` was an nginx container returning a
fake `{"status":"stub"}` payload; it was removed rather than left to be mistaken
for infrastructure. Until P4, `MAIL_ENGINE_ADAPTER=stub` keeps the control plane
fully functional with no mail infrastructure present.

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

Run once, by hand, when P4 makes deployment appropriate. **Do not run this yet.**

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
sudo cp deploy/nginx/app.matemail.online.conf /etc/nginx/sites-available/matemail
sudo ln -s /etc/nginx/sites-available/matemail /etc/nginx/sites-enabled/
sudo certbot --nginx -d app.matemail.online -d matemail.online
sudo nginx -t && sudo systemctl reload nginx
```

Resulting layout — deliberately minimal:

```
/opt/MateMail/
├── docker-compose.yml
├── .env                    (0600)
├── .env.bak.<timestamp>    (last 10, written by each deploy)
└── backups/                (pre-deploy database dumps)
```

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
