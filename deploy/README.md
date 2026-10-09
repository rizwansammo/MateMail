# MateMail — Deployment Assets

**Production target: matemail.pro · 2026-10-09**

Current operating instructions: [Deployment](../docs/DEPLOYMENT.md),
[Architecture](../docs/ARCHITECTURE.md) and
[Backup/Azure DR](../docs/BACKUP_RESTORE.md).

| Repository directory | Production destination |
|---|---|
| `deploy/docker-compose.yml`, `deploy/env.production.example` | `/opt/MateMail/app/` |
| `deploy/native-engine/` | `/opt/MateMail/engine/deploy/native-engine/` |
| `deploy/monitoring/` | `/opt/MateMail/monitoring/` |
| `deploy/backup/` | `/opt/MateMail/backup/` |
| `deploy/custom-hosts/` | Host worker under `/usr/local/libexec/`, secrets in `/etc/matemail/` |

**Warning:** `deploy/nginx/*matemail.online*` are historical artifacts from
the retired hostname architecture. **Do not install or copy those templates**
onto a current server. The live fixed-host vhosts are host-native nginx
configuration; verified customer domains are provisioned by the root worker.

Production app images are built on GitHub Actions and pinned to exact Git
commit-SHA tags; Native Engine images are deployed separately with pinned
digests. **Merging into main never automatically redeploys production.**
Use the manual deployment workflow only for approved releases.

Pre-deploy database dumps must target
`/opt/MateMail/backup/pre-deploy/`. Do not use the removed
`/opt/MateMail/backups` path. For disaster recovery, Azure whole-server backup
and the local Restic repository are separate systems.
