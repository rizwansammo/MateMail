#!/usr/bin/env bash
# MateMail — full restore drill.
#
# Proves a snapshot can actually be restored, by restoring it: the databases
# are loaded into a throwaway PostgreSQL server and queried, the DKIM keys are
# checked as keys, and every staged file is verified against the checksum the
# manifest recorded when it was written. A backup nobody has restored is a
# guess, and this is what turns it into a fact.
#
# It never touches production. Everything lands in a work directory, the
# database it loads into is a disposable container with no network, and the
# script refuses outright to aim at a path that belongs to the running system.
# Restoring the real deployment is a deliberate operator procedure and is
# written up in docs/BACKUP_RESTORE.md; it is not something a drill should be
# one typo away from doing.
#
#   matemail-restore.sh [--snapshot ID] [--workdir DIR] [--keep]
set -euo pipefail
umask 077

ENV_FILE="${MATEMAIL_BACKUP_ENV:-/opt/MateMailBackup/backup.env}"
[ -r "$ENV_FILE" ] || { echo "restore: cannot read $ENV_FILE" >&2; exit 78; }
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

SNAPSHOT=latest
WORKDIR=/opt/MateMailBackup/drill
KEEP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --snapshot) SNAPSHOT="$2"; shift 2 ;;
        --workdir)  WORKDIR="$2";  shift 2 ;;
        --keep)     KEEP=1; shift ;;
        *) echo "restore: unknown argument $1" >&2; exit 64 ;;
    esac
done

log()  { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
pass() { printf '  PASS  %s\n' "$*"; }
die()  { printf '  FAIL  %s\n' "$*" >&2; exit 1; }

# ── Refuse to aim at production ──────────────────────────────────────────────
# A drill that can be pointed at the live system by editing one argument is a
# loaded gun. These are the paths that would destroy something.
case "$WORKDIR" in
    /|/opt/MateMail|/opt/MateMail/*|/opt/MateMailNative|/opt/MateMailNative/*|/var/lib/docker|/var/lib/docker/*|/etc|/etc/*)
        die "refusing to use $WORKDIR: that is production, not a drill area" ;;
esac

DRILL_DB=matemail-restore-drill-db
cleanup() {
    docker rm -f "$DRILL_DB" > /dev/null 2>&1 || true
    if [ "$KEEP" -eq 0 ]; then rm -rf "$WORKDIR"; fi
}
trap cleanup EXIT

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
chmod 700 "$WORKDIR"

log "restore drill: snapshot $SNAPSHOT into $WORKDIR"

# ── Restore ──────────────────────────────────────────────────────────────────
restic restore "$SNAPSHOT" --target "$WORKDIR" --quiet \
    || die "restic could not restore $SNAPSHOT"

STAGE_REL=$(restic snapshots --json "$SNAPSHOT" \
    | python3 -c 'import sys,json; s=json.load(sys.stdin)[-1]; print([p for p in s["paths"] if "matemail-backup" in p][0])')
STAGE="$WORKDIR$STAGE_REL"
MANIFEST="$STAGE/manifest.json"
[ -r "$MANIFEST" ] || die "restored tree has no manifest at $MANIFEST"
pass "snapshot restored"

# ── Every staged file matches the checksum taken when it was written ─────────
# This is the check that catches silent corruption between backup and restore.
STAGE="$STAGE" python3 - <<'PY' || exit 1
import hashlib, json, os, pathlib, sys

stage = pathlib.Path(os.environ["STAGE"])
manifest = json.loads((stage / "manifest.json").read_text())
bad = []
for rel, meta in sorted(manifest["staged_files"].items()):
    p = stage / rel
    if not p.is_file():
        bad.append(f"{rel}: missing")
        continue
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != meta["sha256"]:
        bad.append(f"{rel}: checksum mismatch")
for line in bad:
    print(f"  FAIL  {line}", file=sys.stderr)
if bad:
    sys.exit(1)
print(f"  PASS  {len(manifest['staged_files'])} staged file(s) match their manifest checksums")
PY

# ── Load both databases into a throwaway server ──────────────────────────────
# `trust` is safe precisely because the container has no network: nothing can
# reach it but the docker exec calls below, and it exists for about a minute.
# It also means this drill has no password to leak.
log "starting disposable PostgreSQL"
docker rm -f "$DRILL_DB" > /dev/null 2>&1 || true
docker run -d --rm --name "$DRILL_DB" --network none \
    -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=drill -e POSTGRES_DB=drill \
    postgres:16.15 > /dev/null || die "could not start drill database"

# Readiness is checked over TCP, not the unix socket. The postgres image runs
# a TEMPORARY server during initdb with listen_addresses empty; it answers on
# the socket, then shuts down to be replaced by the real one. A socket probe
# therefore reports ready during initialisation and the very next command
# fails with "No such file or directory". TCP is open only once the real
# server is up, which is the thing actually being waited for. This drill
# passed once by winning that race, which is worse than failing.
READY=0
for _ in $(seq 1 90); do
    if docker exec "$DRILL_DB" pg_isready -h 127.0.0.1 -U drill -q > /dev/null 2>&1; then
        READY=$((READY + 1))
        [ "$READY" -ge 2 ] && break
    else
        READY=0
    fi
    sleep 1
done
[ "$READY" -ge 2 ] || die "drill database never became ready"

restore_into() {
    local dump="$1" db="$2" label="$3"
    docker exec "$DRILL_DB" createdb -h 127.0.0.1 -U drill "$db" > /dev/null \
        || die "could not create $db"
    docker exec -i "$DRILL_DB" pg_restore -h 127.0.0.1 -U drill -d "$db" --no-owner --no-privileges \
        < "$dump" > /dev/null 2>&1 \
        || die "$label did not restore"
    local tables
    tables=$(docker exec "$DRILL_DB" psql -h 127.0.0.1 -qtAX -U drill -d "$db" \
        -c "select count(*) from information_schema.tables where table_schema='public'")
    [ "$tables" -gt 0 ] || die "$label restored zero tables"
    pass "$label restored: $tables table(s)"
}

restore_into "$STAGE/db/matemail.dump" mm_restored "matemail database"
restore_into "$STAGE/db/native.dump"   native_restored "native engine database"

# ── The restored data says what the manifest said it would ───────────────────
# Restoring without error is not the same as restoring the right rows.
check_count() {
    local db="$1" query="$2" expected="$3" label="$4"
    local actual
    actual=$(docker exec "$DRILL_DB" psql -h 127.0.0.1 -qtAX -U drill -d "$db" -c "$query" | tr -d '[:space:]')
    # A manifest with no expectation is a deficient snapshot, not a pass. The
    # whole point of this check is that the restored rows match what was
    # captured; with nothing to compare against there is no check, and a drill
    # that reports PASS anyway is the exact failure mode P6 exists to remove.
    if [ "$expected" = "None" ] || [ -z "$expected" ]; then
        die "$label: the manifest recorded no expectation, so this snapshot cannot be verified"
    fi
    [ "$actual" = "$expected" ] || die "$label: manifest said $expected, restored $actual"
    pass "$label: $actual"
}

expect() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['contents'][sys.argv[2]][sys.argv[3]])" "$MANIFEST" "$1" "$2"; }

check_count mm_restored     "select count(*) from tenants_tenant"   "$(expect matemail_db tenants)"        "matemail tenants"
check_count mm_restored     "select count(*) from domains_domain"   "$(expect matemail_db domains)"        "matemail domains"
check_count mm_restored     "select count(*) from mailboxes_mailbox" "$(expect matemail_db mailboxes)"     "matemail mailboxes"
check_count native_restored "select count(*) from domain"           "$(expect native_db domains)"          "native domains"
check_count native_restored "select count(*) from mailbox"          "$(expect native_db mailboxes)"        "native mailboxes"
check_count native_restored "select count(*) from retired_mailbox_storage" "$(expect native_db retired_storage)" "native retired storage"
check_count native_restored "select max(version) from schema_version" "$(expect native_db schema_version)" "native schema version"

# ── DKIM keys are keys, not just files ───────────────────────────────────────
# A restored file of the right size that openssl will not parse would sign
# nothing, and we would find that out the first time a customer sent mail.
DKIM_OK=0
DKIM_BAD=0
while IFS= read -r key; do
    if openssl rsa -in "$key" -check -noout > /dev/null 2>&1 \
    || openssl pkey -in "$key" -check -noout > /dev/null 2>&1; then
        DKIM_OK=$((DKIM_OK + 1))
    else
        DKIM_BAD=$((DKIM_BAD + 1))
        echo "  FAIL  unusable DKIM key: $(basename "$key")" >&2
    fi
done < <(find "$STAGE/dkim" -name '*.key' -type f)
[ "$DKIM_BAD" -eq 0 ] || die "$DKIM_BAD DKIM key(s) did not restore as usable keys"
[ -r "$STAGE/dkim/selectors.map" ] || die "DKIM selectors.map did not restore"
# Compared against the manifest, so keys going missing between the backup
# and the restore is a failure rather than a smaller number nobody reads.
WANT_KEYS=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['dkim']['keys'])" "$MANIFEST")
[ "$DKIM_OK" = "$WANT_KEYS" ] \
    || die "DKIM: manifest recorded $WANT_KEYS key(s), $DKIM_OK restored usably"
pass "DKIM: $DKIM_OK usable key(s) matching the manifest, selectors.map present"

# ── Mail storage ─────────────────────────────────────────────────────────────
VMAIL_REL=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['roots']['vmail'])" "$MANIFEST")
VMAIL_RESTORED="$WORKDIR$VMAIL_REL"
[ -d "$VMAIL_RESTORED" ] || die "mail storage did not restore to $VMAIL_RESTORED"
GOT_FILES=$(find "$VMAIL_RESTORED" -type f | wc -l)
WANT_FILES=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['vmail']['files'])" "$MANIFEST")
[ "$GOT_FILES" = "$WANT_FILES" ] || die "mail storage: manifest said $WANT_FILES file(s), restored $GOT_FILES"
pass "mail storage: $GOT_FILES file(s) restored, matching the manifest"

# ── Recovery configuration ───────────────────────────────────────────────────
CONFIG_FILES_RESTORED=$(find "$STAGE/config" -type f | wc -l)
[ "$CONFIG_FILES_RESTORED" -gt 0 ] || die "no recovery configuration restored"
pass "recovery configuration: $CONFIG_FILES_RESTORED file(s)"

log "restore drill PASSED for snapshot $SNAPSHOT"
