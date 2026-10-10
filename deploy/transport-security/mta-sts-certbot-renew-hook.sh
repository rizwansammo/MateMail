#!/bin/sh
# Reload Nginx ONLY after an MTA-STS certificate managed by this worker renews.
# This does not issue certificates, modify DNS, start workers or enable timers.
set -eu
lineage="${RENEWED_LINEAGE:-}"
case "$lineage" in
  /etc/letsencrypt/live/mta-sts.*) ;;
  *) exit 0 ;;
esac
host=${lineage##*/}
case "$host" in
  *[!a-z0-9.-]*|.*|*..*|*.) exit 0 ;;
esac
[ "${host#mta-sts.}" != "$host" ] || exit 0
site="/etc/nginx/sites-available/matemail-transport-sts-${host}.conf"
enabled="/etc/nginx/sites-enabled/matemail-transport-sts-${host}.conf"
[ -f "$site" ] && [ -L "$enabled" ] || exit 0
[ "$(readlink -f -- "$enabled")" = "$site" ] || exit 0
[ "$(head -n 1 "$site")" = "# Managed by MateMail P4-C dynamic MTA-STS" ] || exit 0
/usr/sbin/nginx -t
/bin/systemctl reload nginx
