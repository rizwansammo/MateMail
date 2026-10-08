#!/usr/bin/env bash
# MateMail — one production backup run.
#
# WHAT THIS PROTECTS
#   Two PostgreSQL databases, every customer Maildir including the retired
#   storage NE4 keeps after a mailbox is deleted, the DKIM private keys, and
#   the configuration and secrets needed to stand the deployment back up.
#
# WHAT IT DELIBERATELY DOES NOT PROTECT, AND WHY
#   ClamAV signatures and Rspamd's compiled maps are caches that rebuild
#   themselves, and both churn daily — backing them up would cost more
#   repository growth than everything that matters put together. Redis holds
#   rate-limit counters and Celery's broker state, which are meaningless a
#   minute later. The Postfix queue and the Dovecot index volume are worse than
#   useless: restoring a stale queue re-delivers or resurrects mail, and stale
#   indexes laid over restored Maildirs show the user corruption. Indexes are
#   rebuilt with `doveadm force-resync` at restore time instead.
#
#   That list is recorded in every manifest, so adding a volume to the stack
#   without classifying it shows up as a difference rather than as silence.
#
# ATOMICITY
#   A restic snapshot comes into existence only when the backup completes, so
#   there is no such thing as a half-written one. Everything before that point
#   is staged under /run (tmpfs, root-only) and wiped on exit whether the run
#   succeeded or failed. A failed run therefore leaves the repository exactly
#   as it was, and exits non-zero so systemd records a failure.
set -euo pipefail
umask 077

ENV_FILE="${MATEMAIL_BACKUP_ENV:-/opt/MateMail/backup/backup.env}"
[ -r "$ENV_FILE" ] || { echo "backup: cannot read $ENV_FILE" >&2; exit 78; }
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# A run with no cache still works, so this is not about correctness. It is
# about a nightly prune quietly degrading to a full repository walk once
# there is real mail in here, which is the kind of thing that is noticed
# months later as "backups take all night now".
[ -n "${RESTIC_CACHE_DIR:-}" ] || { echo "backup: RESTIC_CACHE_DIR is not set in $ENV_FILE" >&2; exit 78; }

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "FAILED: $*" >&2; exit 1; }

# ── Single run at a time ─────────────────────────────────────────────────────
# The timer catches up after downtime with Persistent=true, and an operator may
# run this by hand at the same moment. Two runs staging into the same directory
# would corrupt each other's dumps.
exec 9>/run/matemail-backup.lock
flock -n 9 || { log "another backup run holds the lock; nothing to do"; exit 0; }

# ── Staging ──────────────────────────────────────────────────────────────────
# /run is tmpfs: the unencrypted database dumps and the copied secrets never
# touch a disk, and never outlive a reboot. The path is fixed rather than
# PID-derived so that the paths inside snapshots are stable and restores are
# predictable; the lock above is what makes a fixed path safe.
STAGE_ROOT=/run/matemail-backup
STAGE="$STAGE_ROOT/stage"

# Nothing is ever mounted under STAGE_ROOT — it is plain tmpfs holding copies —
# so this recursive delete cannot reach customer mail. The vmail volume is read
# in place and is never staged.
cleanup() { rm -rf "$STAGE_ROOT"; }
trap cleanup EXIT

rm -rf "$STAGE_ROOT"
mkdir -p "$STAGE"/db "$STAGE"/dkim "$STAGE"/config
chmod 700 "$STAGE_ROOT" "$STAGE"

STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
log "backup starting"

# ── Databases ────────────────────────────────────────────────────────────────
# The dump runs inside the database container, so the credentials come from the
# container's own environment and never appear in a host process listing. The
# server's own pg_dump is used rather than the host's, which keeps the dump and
# the eventual restore on the same PostgreSQL version.
#
# `pg_restore --list` is the acceptance test for the dump: a truncated or
# empty-but-nonzero file parses as an archive only if it really is one. Without
# it a backup that captured 40 bytes of an error message would look successful.
dump_db() {
    local dir="$1" service="$2" out="$3" label="$4"
    local part="$out.part"

    log "dumping $label"
    ( cd "$dir" && docker compose exec -T "$service" \
        sh -ec 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' < /dev/null ) > "$part" \
        || die "pg_dump failed for $label"

    [ -s "$part" ] || die "$label dump is empty"
    pg_restore --list "$part" > /dev/null 2>&1 \
        || die "$label dump is not a readable PostgreSQL archive"

    mv "$part" "$out"
    log "  $label: $(stat -c %s "$out") bytes, $(pg_restore --list "$out" | grep -c '^[0-9]') objects"
}

dump_db "$MATEMAIL_DIR" "$MATEMAIL_DB_SERVICE" "$STAGE/db/matemail.dump" "matemail database"
dump_db "$NATIVE_DIR"   "$NATIVE_DB_SERVICE"   "$STAGE/db/native.dump"   "native engine database"

# ── DKIM private keys ────────────────────────────────────────────────────────
# Read through a helper container mounting the named volume, so this does not
# depend on Docker's internal storage layout. Keys land in the staging tmpfs and
# from there go only into the encrypted repository; they are never written to a
# disk in the clear and never printed.
# `docker run -v name:/path` CREATES a volume that does not exist rather
# than failing, so without this check a renamed or lost DKIM volume produced
# a perfectly successful backup containing no keys at all. Existence is
# asserted before anything is allowed to mount it.
log "copying DKIM keys"
docker volume inspect "$DKIM_VOLUME" > /dev/null 2>&1 \
    || die "no such volume: $DKIM_VOLUME (mounting it would create an empty one)"
docker run --rm \
    -v "$DKIM_VOLUME":/src:ro \
    -v "$STAGE/dkim":/dst \
    "$HELPER_IMAGE" sh -ec 'cp -a /src/. /dst/' \
    || die "could not read DKIM volume $DKIM_VOLUME"
DKIM_KEYS=$(find "$STAGE/dkim" -name '*.key' -type f | wc -l)
export DKIM_KEYS DKIM_VOLUME
log "  $DKIM_KEYS key file(s), $(find "$STAGE/dkim" -type f | wc -l) file(s) total"

# ── Configuration and secrets needed to rebuild ──────────────────────────────
# Flattened into config/ under a name that records where each file came from,
# because restoring is easier when a file says where it belongs.
log "copying recovery configuration"
CONFIG_COUNT=0
CONFIG_MISSING=""
for f in $CONFIG_FILES; do
    if [ -r "$f" ]; then
        cp -p "$f" "$STAGE/config/$(echo "${f#/}" | tr / _)"
        CONFIG_COUNT=$((CONFIG_COUNT + 1))
    else
        CONFIG_MISSING="$CONFIG_MISSING $f"
    fi
done
log "  $CONFIG_COUNT file(s)${CONFIG_MISSING:+, missing:$CONFIG_MISSING}"

# ── Mail storage ─────────────────────────────────────────────────────────────
# Resolved from the NAMED volume rather than assuming where Docker keeps it.
# Read in place: copying every Maildir into a staging area first would double
# the I/O and the disk for no benefit, since restic deduplicates either way.
#
# Reading a live Maildir is safe. Delivery commits a message by renaming it into
# new/, which is atomic, so a snapshot taken mid-delivery simply does not
# include that message — it never contains half of one.
VMAIL_SRC=$(docker volume inspect "$VMAIL_VOLUME" --format '{{.Mountpoint}}') \
    || die "no such volume: $VMAIL_VOLUME"
[ -d "$VMAIL_SRC" ] || die "vmail volume $VMAIL_VOLUME has no mountpoint"
VMAIL_BYTES=$(du -sb "$VMAIL_SRC" | cut -f1)
VMAIL_FILES=$(find "$VMAIL_SRC" -type f | wc -l)
log "mail storage: $VMAIL_FILES file(s), $VMAIL_BYTES bytes"

# ── Manifest ─────────────────────────────────────────────────────────────────
# Written before the snapshot so it is inside it. It records what was captured,
# what was deliberately left out, and a checksum of every staged file, which is
# what the restore drill verifies against rather than trusting restic's word.
log "writing manifest"
# Counted with psql's own field separator rather than SQL string
# concatenation: ":" in SQL is a quoted IDENTIFIER, not a string literal, and the
# first version of this used it, so every count silently came back empty.
#
# A failure here is fatal. These numbers are what the restore drill checks the
# restored databases against, so a manifest that cannot say what it captured
# leaves the drill with nothing to verify, and turns the proof into a formality.
count_rows() {
    local dir="$1" service="$2" query="$3" label="$4" out
    out=$( ( cd "$dir" && docker compose exec -T "$service" \
        sh -ec "psql -qtAX -F: -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c '$query'" < /dev/null ) ) \
        || die "could not count $label for the manifest"
    out=$(printf '%s' "$out" | tr -d '[:space:]')
    [ -n "$out" ] || die "counting $label returned nothing"
    printf '%s' "$out"
}

MATEMAIL_COUNTS=$(count_rows "$MATEMAIL_DIR" "$MATEMAIL_DB_SERVICE" \
    "select (select count(*) from tenants_tenant), (select count(*) from domains_domain), (select count(*) from mailboxes_mailbox)" \
    "matemail rows")
NATIVE_COUNTS=$(count_rows "$NATIVE_DIR" "$NATIVE_DB_SERVICE" \
    "select (select count(*) from domain), (select count(*) from mailbox), (select count(*) from retired_mailbox_storage), (select max(version) from schema_version)" \
    "native engine rows")
log "  matemail $MATEMAIL_COUNTS / native $NATIVE_COUNTS"

STAGE="$STAGE" VMAIL_SRC="$VMAIL_SRC" STARTED="$STARTED" \
VMAIL_BYTES="$VMAIL_BYTES" VMAIL_FILES="$VMAIL_FILES" \
MATEMAIL_COUNTS="$MATEMAIL_COUNTS" NATIVE_COUNTS="$NATIVE_COUNTS" \
OFFSITE_REPOSITORY="${OFFSITE_REPOSITORY:-}" \
python3 - <<'PY'
import hashlib, json, os, pathlib, socket

stage = pathlib.Path(os.environ["STAGE"])
files = {}
for p in sorted(stage.rglob("*")):
    if p.is_file() and p.name != "manifest.json":
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        files[str(p.relative_to(stage))] = {"sha256": h.hexdigest(),
                                            "bytes": p.stat().st_size}

mm = os.environ["MATEMAIL_COUNTS"].strip().split(":")
nat = os.environ["NATIVE_COUNTS"].strip().split(":")


def at(seq, i):
    try:
        return int(seq[i])
    except (IndexError, ValueError):
        return None


manifest = {
    "format": 1,
    "started_at": os.environ["STARTED"],
    "host": socket.gethostname(),
    "roots": {
        "stage": str(stage),
        "vmail": os.environ["VMAIL_SRC"],
    },
    "staged_files": files,
    "dkim": {
        "volume": os.environ.get("DKIM_VOLUME", ""),
        "keys": int(os.environ["DKIM_KEYS"]),
    },
    "vmail": {
        "volume": os.environ.get("VMAIL_VOLUME", "matemail_native_vmail"),
        "files": int(os.environ["VMAIL_FILES"]),
        "bytes": int(os.environ["VMAIL_BYTES"]),
    },
    "contents": {
        "matemail_db": {"tenants": at(mm, 0), "domains": at(mm, 1),
                        "mailboxes": at(mm, 2)},
        "native_db": {"domains": at(nat, 0), "mailboxes": at(nat, 1),
                      "retired_storage": at(nat, 2), "schema_version": at(nat, 3)},
    },
    "offsite": bool(os.environ.get("OFFSITE_REPOSITORY")),
    # Recorded so that a volume added to the stack later and never classified
    # shows up as a difference against this list instead of being forgotten.
    "excluded_volumes": {
        "matemail_native_clamav_db": "signature cache; freshclam re-downloads",
        "matemail_native_rspamd": "compiled maps and rrd statistics; cache",
        "matemail_native_redis": "rate-limit counters; ephemeral",
        "matemail_redis_data": "celery broker and results; ephemeral",
        "matemail_native_postfix_queue": "in-flight mail; restoring a stale "
                                         "queue re-delivers or resurrects mail",
        "matemail_native_vmail_index": "dovecot indexes; rebuilt with "
                                       "force-resync, stale ones show corruption",
        "matemail_native_tls": "empty; TLS certificates belong to host certbot "
                               "and are reissued rather than restored",
        "matemail_native_auth": "empty; holds no state in the current engine "
                                "and nothing reads it",
        "matemail_native_pgdata": "captured as a pg_dump instead",
        "matemail_postgres_data": "captured as a pg_dump instead",
    },
}
(stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
PY
[ -s "$STAGE/manifest.json" ] || die "manifest was not written"

# ── Snapshot ─────────────────────────────────────────────────────────────────
if [ ! -e "$RESTIC_REPOSITORY/config" ]; then
    log "initialising repository"
    restic init > /dev/null || die "could not initialise repository"
fi

log "writing snapshot"
restic backup --quiet \
    --tag matemail \
    --tag "started=$STARTED" \
    "$STAGE" "$VMAIL_SRC" \
    || die "restic backup failed; repository is unchanged"

SNAPSHOT=$(restic snapshots --json latest --tag matemail \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)[-1]["short_id"])')
log "snapshot $SNAPSHOT"

# ── Verify what was actually written ─────────────────────────────────────────
# "restic backup exited 0" is not evidence that the snapshot contains anything.
# Listing it and requiring the files we care about is.
log "verifying snapshot contents"
LISTING=$(restic ls "$SNAPSHOT")
for required in "$STAGE/manifest.json" "$STAGE/db/matemail.dump" "$STAGE/db/native.dump"; do
    grep -qxF "$required" <<< "$LISTING" || die "snapshot $SNAPSHOT is missing $required"
done
grep -qxF "$VMAIL_SRC" <<< "$LISTING" || die "snapshot $SNAPSHOT is missing mail storage"
restic check --quiet || die "repository failed its integrity check"

# ── Offsite ──────────────────────────────────────────────────────────────────
if [ -n "${OFFSITE_REPOSITORY:-}" ]; then
    log "copying to offsite repository"
    restic -r "$OFFSITE_REPOSITORY" --password-file "$OFFSITE_PASSWORD_FILE" \
        copy --from-repo "$RESTIC_REPOSITORY" --from-password-file "$RESTIC_PASSWORD_FILE" \
        --quiet latest \
        || die "offsite copy failed"
    log "  offsite copy complete"
else
    log "WARNING: no offsite repository is configured. $RESTIC_REPOSITORY is on"
    log "         the same host as production, so it survives deletion and"
    log "         corruption but not the loss of this machine. That is retention,"
    log "         not disaster recovery."
fi

# ── Retention ────────────────────────────────────────────────────────────────
# Scoped to this tag in this dedicated repository. Nothing else on the host is
# reachable from here.
log "applying retention (${KEEP_DAILY}d/${KEEP_WEEKLY}w/${KEEP_MONTHLY}m)"
restic forget --quiet --prune --tag matemail \
    --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY" \
    || die "retention failed"

log "backup complete: snapshot $SNAPSHOT"
