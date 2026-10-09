# MateMail — Safe Production Deployment

**Current revision: 2026-10-09 · Ubuntu MateServer · matemail.pro**

This replaces the pre-Native / Mailcow production instructions. Retired
`deploy/nginx/*matemail.online*` templates must **not** be installed.

## Components

| Component | Live directory |
|---|---|
| Backend, frontend, app PostgreSQL, Redis, Celery | `/opt/MateMail/app` |
| Native mail engine, native PostgreSQL/Redis | `/opt/MateMail/engine/deploy/native-engine` |
| Prometheus, Grafana, Alertmanager | `/opt/MateMail/monitoring` |
| Local encrypted Restic | `/opt/MateMail/backup` |
| Azure disaster-recovery tooling | `/opt/mateserver-backup` |
| Custom-host worker | `/usr/local/libexec/matemail-custom-host-provisioner` |
| Custom-host worker secrets | `/etc/matemail/custom-host-provisioner.env` |

Root-owned .env and secret files are **not** sourced from GitHub; permission
must remain 0600. The VPS does not require a source Git checkout.

## Production hostnames

- Website `matemail.pro`
- Hub `hub.matemail.pro` (with `portal.matemail.pro` redirect)
- PostBox `postbox.matemail.pro`
- Platform Console `platform.matemail.pro`
- Autodiscover `autodiscover.matemail.pro`
- SMTP and IMAP `mx.matemail.pro` with correct A/PTR and TLS cert
- Sender identity `noreply@mail.matemail.pro`, DKIM `mm1`
- Customer custom-host CNAME target `custom.matemail.pro`

## Deployment policy

1. Review the PR, CI results, database migrations and pinned image revisions.
2. Merge an approved change into `main`. **Merge does not deploy** the app.
   GHCR publication and deployment are distinct.
3. For a real app deployment, use the manual **Deploy to MateServer** workflow
   at the exact validated commit SHA. It validates Compose, saves a pre-deploy
   database dump in `/opt/MateMail/backup/pre-deploy/`, preserves .env and has
   rollback logic. Do not run a manual app deployment for docs-only changes.
4. The Native Engine uses its **own** controlled image workflow and separately
   digest-pinned Postfix/Dovecot/Unbound images; Rspamd is pinned separately.
   Confirm Native API compatibility before app cutovers. Never re-deploy the
   Native stack from a stale default tag.
5. Verify HTTP 200 canonical hosts, authenticated SMTP 587, IMAP 993, mail
   queue, service health, sender SPF/DKIM/DMARC and inbound reply as needed.
   Tenant capabilities require their own acceptance tests.

## Read-only server checks

```bash
sudo docker ps --filter name=matemail --format '{{.Names}}|{{.Status}}'
sudo docker exec matemail-native-postfix postqueue -p
sudo nginx -t
sudo systemctl list-timers --all | grep -E 'matemail|mateserver-backup'
(cd /opt/MateMail/app && sudo docker compose --env-file .env config -q)
(cd /opt/MateMail/engine/deploy/native-engine && sudo docker compose --env-file .env config -q)
(cd /opt/MateMail/monitoring && sudo docker compose --env-file .env config -q)
```

Never run `docker compose down -v`, delete customer Maildirs, rotate DKIM
keys or overwrite the production .env in the course of routine synchronization.

The authorized whole-server recovery instructions are in
`/opt/mateserver-backup/DR-RUNBOOK.md`. See
[Backup and Restore](BACKUP_RESTORE.md) before operating recovery tooling.
