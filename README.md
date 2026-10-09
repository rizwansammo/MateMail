# MateMail

**Business email hosting platform by NetaMate Solutions** · Production: **matemail.pro**

MateMail combines a multi-tenant organization **Hub**, **PostBox** webmail, a
Platform Console and a standalone **Native Mail Engine** based on Postfix,
Dovecot and Rspamd. Application and mail-engine PostgreSQL/Redis instances are
isolated. The host serves HTTPS with nginx/Certbot; the domain provisioner also
uses host nginx, not Caddy.

## Current canonical endpoints

| Surface | Production endpoint |
|---|---|
| Website | `https://matemail.pro` |
| Hub | `https://hub.matemail.pro` |
| PostBox | `https://postbox.matemail.pro` |
| Platform Console | `https://platform.matemail.pro` |
| Client MX / IMAPS / submission | `mx.matemail.pro` (25, 993, 587) |
| Platform mail identity | `mail.matemail.pro` |
| Custom-host DNS CNAME | `custom.matemail.pro` |

The old domain migration notes are retained for historical audit only. Do not
apply retired Nginx vhosts or Mailcow-era setup procedures.

## Production structure

```text
/opt/MateMail/app/                  # Django, Next.js, Celery, PostgreSQL, Redis
/opt/MateMail/engine/deploy/native-engine/
/opt/MateMail/monitoring/           # Prometheus, Grafana, Alertmanager
/opt/MateMail/backup/               # local Restic
/opt/mateserver-backup/             # independent Azure whole-server DR
```

Application images are SHA-pinned on GHCR. Native Engine image upgrades are
**separate** and explicitly digest-pinned. GitHub merges are **not** automatic
production deployments.

## Authoritative operations documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Safe deployment](docs/DEPLOYMENT.md)
- [Backup and Azure DR](docs/BACKUP_RESTORE.md)
- [Native Engine security upgrade](docs/NATIVE_ENGINE_SECURITY_UPGRADE_PHASE3_20261009.md)
- [Customer custom domain design](docs/CUSTOM_DOMAINS.md)
- [Operations monitoring](docs/MONITORING.md)

**Dated acceptance (2026-10-09):** the four core Native engines have been
upgraded; 21/21 MateMail containers healthy; Gmail ↔ MateMail real round-trip
passed with Google SPF, DKIM, DMARC PASS. Tenant collaboration flows require
separate acceptance. Do not mistake a dated health result for a perpetual SLA.
