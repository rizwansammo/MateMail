#!/usr/bin/env bash
# Files-only staging. Does not enable timer or touch production DNS/mail.
set -Eeuo pipefail
[ "$(id -u)" = 0 ] || { echo "Run as root" >&2; exit 1; }
[ "${1:-}" = "--install-only" ] || { echo "Usage: sudo bash install.sh --install-only" >&2; exit 2; }
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for bin in python3 nginx certbot systemctl install; do command -v "$bin" >/dev/null; done
/usr/sbin/nginx -t
python3 -m py_compile "$SOURCE/provisioner.py"
sh -n "$SOURCE/mta-sts-certbot-renew-hook.sh"
install -d -o root -g root -m 0755 /usr/local/libexec
install -d -o root -g root -m 0700 /etc/matemail
install -m 0755 "$SOURCE/provisioner.py" /usr/local/libexec/matemail-transport-sts-provisioner
install -m 0644 "$SOURCE/systemd/matemail-transport-sts-provisioner.service" /etc/systemd/system/
install -m 0644 "$SOURCE/systemd/matemail-transport-sts-provisioner.timer" /etc/systemd/system/
# Certbot's renewal requires an Nginx reload to present the renewed certificate.
# The hook is strictly scoped to this worker's own marker and enabled vhost.
install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy
install -m 0755 "$SOURCE/mta-sts-certbot-renew-hook.sh" /etc/letsencrypt/renewal-hooks/deploy/matemail-mta-sts-nginx.sh
systemctl daemon-reload
echo "MTA-STS worker staged. Timer NOT enabled or started. P4-C.F owns activation."
