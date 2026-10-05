#!/bin/sh
# Reload nginx after a MateMail-generated custom-host certificate renews.
# Certbot executes every deploy hook after every successful renewal, so this
# hook must prove the lineage belongs to our generated vhost before acting.
set -eu

lineage="${RENEWED_LINEAGE:-}"
[ -n "$lineage" ] || exit 0

case "$lineage" in
  /etc/letsencrypt/live/*) ;;
  *) exit 0 ;;
esac

hostname=$(basename -- "$lineage")
case "$hostname" in
  *[!a-z0-9.-]*|.*|*..*|*.) exit 0 ;;
esac

site="/etc/nginx/sites-available/matemail-custom-${hostname}.conf"
[ -f "$site" ] || exit 0

# Only a file created by the MateMail provisioner is allowed to trigger this
# path. A same-named certificate owned by another application is unrelated.
first_line=$(head -n 1 "$site" 2>/dev/null || true)
[ "$first_line" = "# MATEMAIL CUSTOM HOST v1" ] || exit 0

/usr/sbin/nginx -t
/bin/systemctl reload nginx
