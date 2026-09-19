#!/usr/bin/env bash
# MateMail — restore one mailbox from a snapshot.
#
# The common real request is not "the server died", it is "I deleted a folder
# last Tuesday". That mailbox is usually still in service, which makes this the
# most dangerous script here: restoring three-day-old mail over a mailbox
# somebody has been using since would destroy the mail that arrived in between,
# and it would look like a successful restore while doing it.
#
# So the default is to restore ALONGSIDE the live mailbox and hand the operator
# a path. Writing into a mailbox that holds messages requires --in-place, and if
# it holds messages, also --force; and even then the current Maildir is moved
# aside rather than deleted. There is no argument to this script that destroys
# mail.
#
#   matemail-restore-mailbox.sh <address> [--snapshot ID] [--storage-id ID]
#                               [--in-place] [--force]
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/guard.sh
. "$HERE/lib/guard.sh"

ENV_FILE="${MATEMAIL_BACKUP_ENV:-/opt/MateMailBackup/backup.env}"
[ -r "$ENV_FILE" ] || { echo "restore-mailbox: cannot read $ENV_FILE" >&2; exit 78; }
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

ADDRESS=""
SNAPSHOT=latest
STORAGE_ID=""
IN_PLACE=0
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --snapshot)   SNAPSHOT="$2"; shift 2 ;;
        --storage-id) STORAGE_ID="$2"; shift 2 ;;
        --in-place)   IN_PLACE=1; shift ;;
        --force)      FORCE=1; shift ;;
        -*) echo "restore-mailbox: unknown option $1" >&2; exit 64 ;;
        *)  ADDRESS="$1"; shift ;;
    esac
done
[ -n "$ADDRESS" ] || { echo "usage: restore-mailbox <address> [options]" >&2; exit 64; }

# Validated before it is ever interpolated into SQL or a path.
[[ "$ADDRESS" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] \
    || { echo "restore-mailbox: not an email address: $ADDRESS" >&2; exit 64; }

log()  { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die()  { echo "restore-mailbox: $*" >&2; exit 1; }

STAMP=$(date -u +%Y%m%dT%H%M%SZ)

# ── Find the storage this address used ───────────────────────────────────────
# Storage is addressed by the immutable storage_id NE4 introduced, not by the
# address, so a deleted-and-recreated address does not collide with what its
# previous owner left behind. An active mailbox is in `mailbox`; a deleted one
# whose mail NE4 retained is in `retired_mailbox_storage`.
native_query() {
    ( cd "$NATIVE_DIR" && docker compose exec -T "$NATIVE_DB_SERVICE" \
        sh -ec "psql -qtAX -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"$1\"" < /dev/null ) 2>/dev/null
}

if [ -z "$STORAGE_ID" ]; then
    ROW=$(native_query "select d.name||' '||m.storage_id from mailbox m join domain d on d.id=m.domain_id where m.address='$ADDRESS'" | head -n1)
    SOURCE=active
    if [ -z "$ROW" ]; then
        MATCHES=$(native_query "select domain_name||' '||storage_id from retired_mailbox_storage where address='$ADDRESS' order by retired_at desc")
        COUNT=$(printf '%s\n' "$MATCHES" | grep -c . || true)
        [ "$COUNT" -gt 0 ] || die "no active or retired storage is recorded for $ADDRESS. If the database is also lost, restore it first, or pass --storage-id."
        if [ "$COUNT" -gt 1 ]; then
            echo "restore-mailbox: $ADDRESS has $COUNT retired storage directories:" >&2
            printf '%s\n' "$MATCHES" | sed 's/^/  /' >&2
            die "pass --storage-id to say which one"
        fi
        ROW="$MATCHES"
        SOURCE=retired
    fi
    DOMAIN=${ROW%% *}
    STORAGE_ID=${ROW##* }
else
    DOMAIN=${ADDRESS##*@}
    SOURCE=explicit
fi
[ -n "$DOMAIN" ] && [ -n "$STORAGE_ID" ] || die "could not resolve storage for $ADDRESS"
log "$ADDRESS -> $DOMAIN/$STORAGE_ID ($SOURCE)"

VMAIL_SRC=$(docker volume inspect "$VMAIL_VOLUME" --format '{{.Mountpoint}}')
SNAP_PATH="$VMAIL_SRC/$DOMAIN/$STORAGE_ID"
LIVE_PATH="$SNAP_PATH"

# ── Pull just that mailbox out of the snapshot ───────────────────────────────
EXTRACT="/run/matemail-restore-mailbox.$STAMP"
rm -rf "$EXTRACT"; mkdir -p "$EXTRACT"; chmod 700 "$EXTRACT"
trap 'rm -rf "$EXTRACT"' EXIT

log "extracting from snapshot $SNAPSHOT"
restic restore "$SNAPSHOT" --target "$EXTRACT" --include "$SNAP_PATH" --quiet \
    || die "restic could not restore $SNAP_PATH from $SNAPSHOT"
RESTORED="$EXTRACT$SNAP_PATH"
[ -d "$RESTORED" ] || die "snapshot $SNAPSHOT does not contain $SNAP_PATH"
MSGS=$(find "$RESTORED" -type d \( -name cur -o -name new \) -exec find {} -maxdepth 1 -type f \; 2>/dev/null | wc -l)
log "snapshot holds $MSGS message file(s)"

STATE=$(mailbox_target_state "$LIVE_PATH")
log "live mailbox is $STATE"

# ── Side-by-side restore: the default, and it cannot lose anything ───────────
if [ "$IN_PLACE" -eq 0 ]; then
    DEST="$LIVE_PATH.restored-$STAMP"
    [ -e "$DEST" ] && die "$DEST already exists"
    cp -a "$RESTORED" "$DEST"
    log "restored beside the live mailbox:"
    log "  $DEST"
    log ""
    log "The live mailbox was not touched. Compare the two, move what is wanted"
    log "into place, then remove the restored copy. To have this script write"
    log "into the mailbox itself, re-run it with --in-place."
    exit 0
fi

# ── In-place restore ─────────────────────────────────────────────────────────
if [ "$STATE" = active ] && [ "$FORCE" -eq 0 ]; then
    die "$ADDRESS is an active mailbox and holds mail that is not in this snapshot.
  Restoring in place would overwrite it. Either re-run without --in-place to get
  a copy beside it, or add --force to accept the overwrite. --force still moves
  the current Maildir aside instead of deleting it."
fi

if [ "$STATE" != absent ]; then
    ASIDE="$LIVE_PATH.replaced-$STAMP"
    mv "$LIVE_PATH" "$ASIDE"
    log "current Maildir moved aside: $ASIDE"
fi

mkdir -p "$(dirname "$LIVE_PATH")"
cp -a "$RESTORED" "$LIVE_PATH"
# restic preserves ownership, so the restored tree already carries the vmail
# uid/gid. Re-asserting it from the domain directory covers a restore made into
# a freshly created domain directory.
OWNER=$(stat -c '%u:%g' "$(dirname "$LIVE_PATH")")
chown -R "$OWNER" "$LIVE_PATH"
log "restored in place: $LIVE_PATH (owner $OWNER)"

# ── Rebuild the indexes we deliberately do not back up ───────────────────────
# Dovecot's index volume is excluded from backups because stale indexes over
# restored mail present as corruption to the user. This is the other half of
# that decision: the indexes are rebuilt from the Maildir that was just written.
if [ "$SOURCE" = active ]; then
    log "rebuilding Dovecot indexes"
    ( cd "$NATIVE_DIR" && docker compose exec -T dovecot \
        doveadm force-resync -u "$ADDRESS" '*' < /dev/null ) > /dev/null 2>&1 \
        && log "  indexes rebuilt" \
        || log "  WARNING: force-resync failed. Run it by hand before the user opens this mailbox."
else
    log "mailbox is not provisioned, so there are no indexes to rebuild."
    log "Re-create the mailbox with --storage-id $STORAGE_ID to adopt this storage."
fi

log "done"
