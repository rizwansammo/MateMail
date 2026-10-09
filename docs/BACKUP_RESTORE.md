# MateMail — Backup and Azure Disaster Recovery

**Updated and verified 2026-10-09.** Do not follow older
`/opt/MateMailBackup` instructions. Real credentials and private keys are never
stored in GitHub.

## Two backup systems

| System | Where | Meaning |
|---|---|---|
| Local encrypted Restic | `/opt/MateMail/backup/repo` on same VPS | Targeted MateMail restore, daily `matemail-backup.timer` |
| MateServer Azure DR | `/opt/mateserver-backup/bin/backup.sh` → Azure Blob | Independently scheduled daily whole-server disaster recovery, including MateMail |

The local Restic `OFFSITE_REPOSITORY` is **not configured**. It must not be
described as offsite, but neither should the independent **Azure DR** be
misreported as absent.

## Consolidated MateMail state

```text
/opt/MateMail/app/{.env,docker-compose.yml}
/opt/MateMail/engine/deploy/native-engine/{.env,docker-compose.yml}
/opt/MateMail/monitoring/{.env,collector.env,docker-compose.yml}
/opt/MateMail/backup/{backup.env,restic-password,repo/}
```

Azure DR retains this **nested directory structure** and the host-level
custom-host provisioning executable + secret config. It includes logical
`pg_dump -Fc` for MateMail app and native PostgreSQL (plus other server
databases); persistent maildir, index, DKIM, TLS, Rspamd, Redis, Unbound,
Postfix queue and Grafana volumes; Nginx/Certbot/systemd host configs.
PostgreSQL live data directories are not copied as raw volume backups.
Regenerable ClamAV signatures do not require recovery.

**Important:** a restored historic Postfix queue can resend messages; do not
blindly start outbound SMTP with old queue files. Inspect and quarantine or
discard replayable queue entries during actual recovery.

## Local Restic exclusions (separate from Azure DR)

The local MateMail `matemail-backup.sh` intentionally omits the following
volumes. This is **not** the same volume policy as the whole-server Azure DR
job above. The table must remain aligned with the local Restic manifest's
`excluded_volumes` list.

| Local Restic volume excluded | Why |
|---|---|
| `matemail_native_clamav_db` | ClamAV signature database; freshclam downloads again |
| `matemail_native_rspamd` | Compiled lookup maps and statistics cache |
| `matemail_native_redis` | Ephemeral engine rate-limit counters |
| `matemail_redis_data` | Ephemeral app Celery broker/results |
| `matemail_native_postfix_queue` | In-flight mail must not be blindly replayed |
| `matemail_native_vmail_index` | Rebuild Dovecot indexes after Maildir restore |
| `matemail_native_tls` | Local Restic expects TLS to be restored via host Certbot |
| `matemail_native_auth` | Engine auth socket mount, no persistent state |
| `matemail_native_pgdata` | Use consistent native PostgreSQL logical dumps instead |
| `matemail_postgres_data` | Use consistent app PostgreSQL logical dumps instead |

For **Azure DR**, the Rspamd, engine Redis, Dovecot index, TLS and queue
volumes **are** copied separately from the local Restic strategy. During
actual disaster recovery, verify service compatibility and deliberately
quarantine any restored SMTP queue before SMTP delivery is enabled.

## Azure schedule and verified acceptance

- Scheduled by `mateserver-backup.timer` daily at 03:30 **server local time**,
  plus up to ten minutes of randomized delay.
- Uploads `latest/mateserver-latest.tar.gz` and
  `daily/YYYY-MM-DD/mateserver-full.tar.gz` with SHA256 files.
- Keeps the **30 most recent daily snapshot dates**.
- On **2026-10-09**: 62,804,457-byte archive uploaded to both Azure prefixes.
  Downloaded again; archive SHA256 and internal file checksums passed.
- All 11 logical PostgreSQL dumps were readable. Both MateMail dumps restored
  successfully in a disposable **network-isolated** PostgreSQL 16 instance.
  App 68 tables, Native 13 tables; recovered data counts matched live.
- All three MateMail Compose configurations parsed correctly using
  staged recovered configuration.
- **A complete blank-VPS failover rehearsal was deliberately deferred**.
  Do not call this a verified full-stack production failover.

The updated Azure backup and restore scripts are on MateServer, not in this
source repository. Their pre-change copies and result report are root-only in
`/root/mateserver-dr-matemail-architecture-20261009`.

## Safe checks

```bash
sudo systemctl show mateserver-backup.service -p Result -p ExecMainStatus
sudo systemctl list-timers --all | grep -E 'mateserver-backup|matemail-backup'
sudo docker exec matemail-native-postfix postqueue -p
```

`/opt/mateserver-backup/bin/restore.sh latest --validate-only` verifies
the archive without restoring databases. **Actual `--restore` is destructive**
and is permitted only during a separately authorized recovery on a suitable
blank/isolation host. Root-only local Restic tools are in
`/opt/MateMail/backup`. See the MateServer DR runbook before full recovery.
