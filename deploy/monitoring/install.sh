#!/usr/bin/env bash
# MateMail — install the monitoring stack onto the host.
#
# Run from a copy of this directory on the server. Safe to re-run: it updates
# configuration and dashboards in place and leaves the Grafana password, the
# metric history and any local .env edits alone.
set -euo pipefail
umask 077

DEST=/opt/MateMail/monitoring
SRC="$(cd "$(dirname "$0")" && pwd)"
TEXTFILE=/var/lib/node_exporter/textfile_collector

[ "$(id -u)" = 0 ] || { echo "install: must run as root" >&2; exit 1; }

echo "install: directories"
mkdir -p "$DEST" "$TEXTFILE" /var/lib/matemail-monitoring
# node_exporter runs unprivileged inside its container and reads this
# directory read-only; the collector writes it as root.
chmod 755 "$TEXTFILE"
chmod 700 /var/lib/matemail-monitoring

echo "install: configuration"
for d in prometheus alertmanager grafana collectors systemd logrotate; do
    rm -rf "$DEST/$d"
    cp -r "$SRC/$d" "$DEST/$d"
done
cp "$SRC/docker-compose.yml" "$DEST/docker-compose.yml"
cp "$SRC/.env.example" "$DEST/.env.example"

# Permissions are normalised explicitly rather than inherited from this
# script's umask.
#
# This script runs `umask 077` so that the runtime .env it creates is private.
# That same umask makes every copied file root-only — and Prometheus,
# Alertmanager and Grafana all run as UNPRIVILEGED users inside their
# containers, by design. They then cannot read their own configuration, and
# crash-loop with "permission denied" while the volumes and the compose file
# both look perfectly correct.
#
# None of this configuration is secret: it is scrape targets, alert rules and
# dashboards, all of it committed to the repository. The secrets are .env and
# nothing else, and that is locked down separately below.
find "$DEST" -type d -exec chmod 755 {} +
find "$DEST" -type f -exec chmod 644 {} +
chmod 755 "$DEST/collectors/matemail_collector.py"

# ── Runtime configuration ───────────────────────────────────────────────────
# The Grafana admin password is generated once and never printed. Regenerating
# it on every install would lock the operator out of their own dashboards.
if [ -e "$DEST/.env" ]; then
    echo "install: .env already exists; keeping local settings"
else
    cp "$SRC/.env.example" "$DEST/.env"
    PW=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
    sed -i "s|^GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=${PW}|" "$DEST/.env"
    unset PW
    echo "install: generated a Grafana admin password (not printed)"
    echo "         read it with: sudo grep GRAFANA_ADMIN_PASSWORD $DEST/.env"
fi
chown root:root "$DEST/.env"
chmod 600 "$DEST/.env"

# The collector needs the backup repository's location to report snapshot age.
cat > "$DEST/collector.env" <<'ENV'
# Passed to the collector by systemd. Paths only; no secrets live here.
NATIVE_DIR=/opt/MateMail/engine/deploy/native-engine
MATEMAIL_DIR=/opt/MateMail/app
BACKUP_ENV=/opt/MateMail/backup/backup.env
MATEMAIL_HEALTH_URL=http://127.0.0.1:8020/api/internal/health/
MAIL_HOSTNAME=mx.matemail.pro
MAIL_DOMAIN=matemail.pro
SENDER_DOMAIN=mail.matemail.pro
MX_CHECK_DOMAIN=mail.matemail.pro
MAIL_CERT_NAME=matemail-mail-pro
DKIM_SELECTOR=mm1
PUBLIC_IP=169.58.114.252
ENV
chmod 644 "$DEST/collector.env"

# ── Alert delivery ──────────────────────────────────────────────────────────
# A webhook URL, when one is configured, is written into the Alertmanager
# config at install time rather than committed. With none configured the
# receivers stay destination-less: alerts still fire and are still visible in
# the UI, and nothing is pushed off the box.
WEBHOOK=$(grep -E '^ALERT_WEBHOOK_URL=' "$DEST/.env" | cut -d= -f2- || true)
if [ -n "${WEBHOOK:-}" ]; then
    python3 - "$DEST/alertmanager/alertmanager.yml" "$WEBHOOK" <<'PY'
import sys
path, url = sys.argv[1], sys.argv[2]
text = open(path).read()
text = text.replace(
    '  - name: "default"\n  - name: "critical"\n',
    '  - name: "default"\n'
    '    webhook_configs:\n      - url: "%s"\n        send_resolved: true\n'
    '  - name: "critical"\n'
    '    webhook_configs:\n      - url: "%s"\n        send_resolved: true\n'
    % (url, url))
open(path, "w").write(text)
PY
    echo "install: alert webhook configured"
else
    echo "install: NO alert webhook configured. Alerts fire and are visible in"
    echo "         Alertmanager, but nothing is delivered off this host."
    echo "         Reported as ALERT_RECEIVER_CONFIGURED=NO."
fi

# ── Log rotation ────────────────────────────────────────────────────────────
# Docker on this host has no daemon.json and therefore no log rotation at all.
# Fixing that in daemon.json would need a Docker restart across 72 containers;
# logrotate bounds the same files today and disturbs nothing.
install -m 0644 -o root -g root "$SRC/logrotate/matemail-docker-containers" \
    /etc/logrotate.d/matemail-docker-containers
logrotate --debug /etc/logrotate.d/matemail-docker-containers > /dev/null \
    && echo "install: docker log rotation configured"

# ── Collector ───────────────────────────────────────────────────────────────
echo "install: collector"
install -m 0644 -o root -g root "$SRC/systemd/matemail-collector.service" \
    /etc/systemd/system/
install -m 0644 -o root -g root "$SRC/systemd/matemail-collector.timer" \
    /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now matemail-collector.timer > /dev/null
# Run once now so Prometheus has data on its first scrape rather than a minute
# of nothing that looks like a broken target.
systemctl start matemail-collector.service || true

# ── Stack ───────────────────────────────────────────────────────────────────
echo "install: validating configuration before starting anything"
docker compose -f "$DEST/docker-compose.yml" --env-file "$DEST/.env" config -q
echo "  compose OK"

echo "install: starting"
cd "$DEST"
# --force-recreate, for two independent reasons that both bite here.
#
# 1. This script replaces the configuration directories with `rm -rf` followed
#    by a copy, which creates NEW inodes. A running container's bind mount was
#    resolved at start and still points at the deleted directory, so it keeps
#    reading configuration that no longer exists on disk. Grafana hit exactly
#    this on first deployment: the files were correct, the permissions were
#    correct, and inside the container the directory was unreadable.
#
# 2. Even without that, Compose compares images, environment, mounts and
#    labels — never the CONTENTS of a mounted file. A configuration-only change
#    is invisible to it, so `up -d` would report success and leave the old
#    rules and dashboards running. The Native Engine deploy script solves the
#    same problem by hashing its configuration; here the stack is small enough
#    that recreating it outright is simpler and has no downside, because all
#    state lives in named volumes.
docker compose --env-file "$DEST/.env" up -d --force-recreate

echo "install: done"
docker compose ps --format '{{.Name}}\t{{.Status}}' | sed 's/^/  /'
