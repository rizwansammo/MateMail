# MateMail — Backup and Restore

Written for the person on the other end of an incident. It says what is
protected, what is deliberately not, and exactly what to type.

---

## 1. What exists

| Piece | Path |
|---|---|
| Backup script | `/opt/MateMailBackup/matemail-backup.sh` |
| Full restore drill | `/opt/MateMailBackup/matemail-restore.sh` |
| Single-mailbox restore | `/opt/MateMailBackup/matemail-restore-mailbox.sh` |
| Configuration | `/opt/MateMailBackup/backup.env` (root:root, 0600) |
| Repository password | `/opt/MateMailBackup/restic-password` (root:root, 0600) |
| Local repository | `/opt/MateMailBackup/repo` |
| Schedule | `matemail-backup.timer`, daily 01:30 UTC, `Persistent=true` |

Source of truth is `deploy/backup/` in the repository; the host copy is
installed from it by `install.sh`.

Engine is **restic**: encrypted (AES-256 with Poly1305-AES authentication),
deduplicating, snapshot-based. Nothing in this system invents its own crypto.

---

## 2. The repository password

`restic-password` is the only thing that can decrypt the snapshots. If it is
lost, the backups are lost — there is no recovery path, no vendor, no override.

It is generated once by `install.sh` and never printed. **Store a copy outside
this host**, in the same place the other production secrets live. Do not commit
it. Re-running `install.sh` will not overwrite it.

---

## 3. What is backed up

| State | How it is captured | Why this way |
|---|---|---|
| MateMail database | `pg_dump -Fc` inside the `postgres` container | Version-matched dump, restorable selectively. A copy of the data directory would be a copy of a running server's files. |
| Native engine database | `pg_dump -Fc` inside the native `db` container | Same. Also carries `retired_mailbox_storage`, without which retired Maildirs are anonymous directories. |
| Customer mail | the `matemail_native_vmail` volume, read in place | Maildir commits by atomic rename, so a live read is safe. |
| Retired mailbox storage (NE4) | *inside* the vmail volume | See §7 — it needs the native database too. |
| DKIM private keys | the `matemail_native_dkim` volume | Into the encrypted repository only. Never written to disk in the clear. |
| Recovery configuration | both `.env` files, both `docker-compose.yml`, the two nginx sites, the systemd units | Everything needed to rebuild that is not in Git. |

The unencrypted dumps and the copied secrets exist only under `/run`
(tmpfs, root-only) during the run, and are wiped when it exits either way.

---

## 4. What is deliberately **not** backed up

Backing up everything is not caution, it is a way to make restores slower and
repositories larger without making anything safer. Each of these is a decision:

**Rebuilds itself:**

- `matemail_native_clamav_db` — signature database; freshclam re-downloads it.
  168 MB that changes daily; backing it up would dominate the repository.
- `matemail_native_rspamd` — compiled hyperscan maps and `rspamd.rrd`
  statistics. Verified to hold no learned data.
- `matemail_native_redis` — rate-limit counters. Verified empty of Bayes
  tokens; nothing here is meaningful a minute later.
- `matemail_redis_data` — Celery broker and results.

**Restoring it would cause harm:**

- `matemail_native_postfix_queue` — in-flight mail. Restoring an old queue
  re-delivers mail that was already delivered, or resurrects mail that was
  deliberately cancelled.
- `matemail_native_vmail_index` — Dovecot indexes. Indexes older than the
  Maildir beneath them present to the user as missing and duplicated mail.
  Restores rebuild them instead, with `doveadm force-resync`.

**Captured a better way, or empty:**

- `matemail_native_pgdata`, `matemail_postgres_data` — captured as `pg_dump`.
- `matemail_native_tls`, `matemail_native_auth` — unused; TLS is host certbot's.

Images come from GHCR pinned by commit SHA; source comes from Git. Neither
belongs in a backup.

This list is written into every manifest. A volume added to the stack later and
never classified shows up as a difference rather than as silence.

---

## 5. Offsite — read this before believing you have disaster recovery

`/opt/MateMailBackup/repo` sits on the production host. It protects against
deletion, corruption, ransomware and operator error. **It does not protect
against losing the machine.** That is retention, not disaster recovery.

Set `OFFSITE_REPOSITORY` in `backup.env` to any repository restic understands
(`sftp:`, `s3:`, `rest:`, a mounted volume) with `OFFSITE_PASSWORD_FILE`
alongside it. No provider is assumed or preferred.

Until it is set, every run logs a warning and every manifest records
`"offsite": false`. Check it:

```bash
restic dump latest /run/matemail-backup/stage/manifest.json | python3 -m json.tool | grep offsite
```

---

## 6. Routine operation

```bash
systemctl list-timers matemail-backup.timer     # is it scheduled
systemctl status  matemail-backup.service       # did the last run pass
journalctl -u matemail-backup.service -n 50     # why not
/opt/MateMailBackup/matemail-backup.sh          # run one now
```

A failed run exits non-zero, leaves **no** snapshot, and leaves the repository
exactly as it was. There is no half-written snapshot to clean up.

Inspect the repository:

```bash
set -a; . /opt/MateMailBackup/backup.env; set +a
restic snapshots
restic stats latest
```

Retention: 14 daily, 8 weekly, 6 monthly, applied with `restic forget --prune`
scoped to the `matemail` tag in this repository only.

---

## 7. Restoring one mailbox

The usual request. The mailbox is usually still in use, which is what makes
this dangerous: restoring Tuesday's mail over it would destroy everything that
arrived since, and would look like success.

**Default — restores beside the live mailbox, cannot lose anything:**

```bash
/opt/MateMailBackup/matemail-restore-mailbox.sh user@customer.com
```

It prints a path like `/var/vmail/customer.com/<storage_id>.restored-<ts>`.
The live mailbox is untouched. Compare, move across what is wanted, delete the
copy.

**In place**, when the mailbox is genuinely empty or gone:

```bash
/opt/MateMailBackup/matemail-restore-mailbox.sh user@customer.com --in-place
```

If the mailbox holds mail, this refuses. Adding `--force` accepts the
overwrite — and still moves the current Maildir to `.replaced-<ts>` rather than
deleting it. No argument to this script deletes mail.

After an in-place restore into a provisioned mailbox, indexes are rebuilt
automatically.

**A deleted mailbox.** NE4 keeps the Maildir and records it in
`retired_mailbox_storage`, so the script finds it by address with no extra
arguments. This is why the native database and the mail must be restored from
the same snapshot: the mail is on disk under an opaque `storage_id`, and the
database is the only thing that says whose it was.

**From an older snapshot:** add `--snapshot <id>` (`restic snapshots` lists
them).

---

## 8. Proving the backups work

Run the drill. It restores a snapshot into an isolated directory, verifies every
staged file against the checksum recorded when it was written, loads both
databases into a disposable PostgreSQL container with no network, checks the
restored row counts against the manifest, and checks each DKIM key parses as a
key.

```bash
/opt/MateMailBackup/matemail-restore.sh              # latest
/opt/MateMailBackup/matemail-restore.sh --snapshot 1a2b3c4d --keep
```

It never touches production and refuses a `--workdir` that names any production
path.

**Run it monthly, and after any change to the backup scripts or the stack's
volumes.** A backup that has not been restored is a guess.

---

## 9. Full disaster recovery

Deliberate, manual, and done with a human reading each step — not a script that
is one typo away from running against a live system.

1. **Rebuild the host** to the MateServer model: Ubuntu, Docker, host nginx,
   certbot, UFW.
2. **Get the repository and its password.** Without the password there is
   nothing to do here.
3. **Extract**, keeping the tree:
   ```bash
   restic restore latest --target /restore
   ```
4. **Put the configuration back** from `/restore/run/matemail-backup/stage/config/`.
   Filenames encode their origin: `opt_MateMail_.env` → `/opt/MateMail/.env`.
   Restore modes: both `.env` files are `0600 root:root`.
5. **Start the databases only**, then load the dumps into the empty databases:
   ```bash
   cd /opt/MateMail && docker compose up -d postgres
   docker compose exec -T postgres sh -ec \
     'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --clean --if-exists' \
     < /restore/run/matemail-backup/stage/db/matemail.dump
   ```
   and the same against the native `db` service with `native.dump`.
6. **Put the mail back** into the `matemail_native_vmail` volume, preserving
   ownership and modes:
   ```bash
   docker volume create matemail_native_vmail
   docker run --rm -v matemail_native_vmail:/dst \
     -v /restore/var/lib/docker/volumes/matemail_native_vmail/_data:/src:ro \
     alpine:3.20 sh -ec 'cp -a /src/. /dst/'
   ```
7. **Put the DKIM keys back** into `matemail_native_dkim` the same way. Confirm
   the published DNS records still match the restored selectors before sending;
   if they do not, rotate rather than publish a key that has been sitting in a
   restore directory.
8. **Start everything**, then `deploy.sh` for the native engine so configuration
   hashes are recomputed.
9. **Do not restore** the volumes in §4. Dovecot rebuilds its indexes; let it.
10. **Verify** before pointing DNS at the host: `manage.py check --deploy`,
    native schema version is 4, `restic`-restored mailbox counts match the
    manifest, send one internal test message end to end.

---

## 10. Tenant-facing "backups" in the application

`backend/apps/backups/` exposes `/api/backups/` to tenant admins. It does not
back anything up: `run_backup_task` counts rows, computes `size_mb` from a
formula, records a `storage_location` that is never written, and reports the job
as completed. It has no relationship to anything in this document.

It must not be presented to customers as a backup feature until it is one. See
`docs/TODO.md`.
