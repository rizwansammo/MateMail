#!/usr/bin/env bash
# MateMail — install the backup system onto the host.
#
# Run from a copy of this directory on the server. Safe to re-run: it updates
# the scripts and units in place and leaves the repository, its password and any
# local edits to backup.env alone.
set -euo pipefail
umask 077

DEST=/opt/MateMailBackup
SRC="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" = 0 ] || { echo "install: must run as root" >&2; exit 1; }

echo "install: dependencies"
if ! command -v restic > /dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq restic > /dev/null
fi
restic version | sed 's/^/  /'

echo "install: directories"
mkdir -p "$DEST/lib" "$DEST/repo"
chmod 700 "$DEST" "$DEST/repo"

echo "install: scripts"
install -m 0700 -o root -g root "$SRC/matemail-backup.sh"          "$DEST/"
install -m 0700 -o root -g root "$SRC/matemail-restore.sh"         "$DEST/"
install -m 0700 -o root -g root "$SRC/matemail-restore-mailbox.sh" "$DEST/"
install -m 0600 -o root -g root "$SRC/lib/guard.sh"                "$DEST/lib/"

# ── Repository password ──────────────────────────────────────────────────────
# Generated here, once, and never printed. Overwriting it would make every
# existing snapshot permanently unreadable, so the guard is absolute rather than
# a prompt: if the file exists, it is kept.
if [ -e "$DEST/restic-password" ]; then
    echo "install: repository password already exists; keeping it"
else
    ( umask 077; openssl rand -base64 48 > "$DEST/restic-password" )
    echo "install: generated a new repository password"
    echo "         BACK IT UP SOMEWHERE OFF THIS HOST. Without it the snapshots"
    echo "         cannot be decrypted, by anyone, ever."
fi
chown root:root "$DEST/restic-password"
chmod 600 "$DEST/restic-password"

# ── Configuration ────────────────────────────────────────────────────────────
if [ -e "$DEST/backup.env" ]; then
    echo "install: backup.env already exists; keeping local settings"
    echo "         (compare against backup.env.example for new keys)"
    install -m 0600 -o root -g root "$SRC/backup.env.example" "$DEST/backup.env.example"
else
    install -m 0600 -o root -g root "$SRC/backup.env.example" "$DEST/backup.env"
    install -m 0600 -o root -g root "$SRC/backup.env.example" "$DEST/backup.env.example"
    echo "install: created backup.env from the example"
fi

# Added to an existing backup.env too: an upgrade must not leave the timer
# running cacheless just because the operator already had a config file.
grep -q '^RESTIC_CACHE_DIR=' "$DEST/backup.env" || echo 'RESTIC_CACHE_DIR=/var/cache/restic' >> "$DEST/backup.env"
CACHE=$(grep -E '^RESTIC_CACHE_DIR=' "$DEST/backup.env" | cut -d= -f2)
mkdir -p "${CACHE:-/var/cache/restic}"
chown root:root "${CACHE:-/var/cache/restic}"
chmod 700 "${CACHE:-/var/cache/restic}"
echo "install: restic cache at ${CACHE:-/var/cache/restic}"

echo "install: helper image"
HELPER=$(grep -E '^HELPER_IMAGE=' "$DEST/backup.env" | cut -d= -f2)
docker pull -q "${HELPER:-alpine:3.20}" > /dev/null

echo "install: systemd"
install -m 0644 -o root -g root "$SRC/matemail-backup.service" /etc/systemd/system/
install -m 0644 -o root -g root "$SRC/matemail-backup.timer"   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now matemail-backup.timer > /dev/null
systemctl list-timers matemail-backup.timer --no-pager | sed 's/^/  /'

echo "install: done"
