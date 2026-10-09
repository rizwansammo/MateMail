#!/usr/bin/env bash
# P4-C MateMail MTA-STS transport policy installer.
# Runs on MateServer only. Never changes mail flow or DNS.
# Domain whitelist and independent HTTP/HTTPS stages intentionally prevent
# advertising a policy that clients cannot fetch and authenticate.
set -Eeuo pipefail

usage() {
  echo "Usage: sudo bash install.sh prepare|activate mail.matemail.pro|netamate.com|matedesk.pro" >&2
  exit 2
}
[ "$#" -eq 2 ] || usage
ACTION="$1"
DOMAIN="$2"
case "$ACTION" in prepare|activate) ;; *) usage ;; esac
case "$DOMAIN" in
  mail.matemail.pro|netamate.com|matedesk.pro) ;;
  *) echo "Not an approved mail receiving domain" >&2; exit 3 ;;
esac
[ "$(id -u)" -eq 0 ] || { echo "Must run as root" >&2; exit 3; }

HOST="mta-sts.$DOMAIN"
SERVER_IP="169.58.114.252"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="/var/www/matemail/mta-sts/$DOMAIN"
SITE="/etc/nginx/sites-available/matemail-mta-sts-$DOMAIN"
LINK="/etc/nginx/sites-enabled/matemail-mta-sts-$DOMAIN"
LOCK="/var/lock/matemail-mta-sts.lock"
exec 9>"$LOCK"
flock -n 9 || { echo "MTA-STS install locked" >&2; exit 1; }

[ -f "$SCRIPT_DIR/policy.txt" ] || { echo "Missing versioned policy.txt"; exit 1; }
python3 "$SCRIPT_DIR/validate.py" "$SCRIPT_DIR/policy.txt" "$DOMAIN"
if [ -e "$SITE" ] && ! grep -Fxq "# Managed by MateMail P4-C: $DOMAIN" "$SITE"; then
  echo "Conflicting non-MateMail nginx site; will not overwrite $SITE" >&2
  exit 3
fi
if [ -e "$LINK" ] && [ "$(readlink -f "$LINK")" != "$SITE" ]; then
  echo "Conflicting nginx enabled site" >&2
  exit 3
fi

# Ensure actual MX still matches the policy before enabling anything.
MX="$(dig @1.1.1.1 +short +tries=1 +time=4 MX "$DOMAIN" || true)"
[ -n "$MX" ] || { echo "No MX; refuse MTA-STS for $DOMAIN"; exit 3; }
if printf '%s\n' "$MX" | awk '{print tolower($2)}' | grep -v '^mx[.]matemail[.]pro[.]$'; then
  echo "Unexpected MX in $DOMAIN; policy does not cover all MX hosts" >&2
  exit 3
fi

# Source policy is LF in Git, served RFC 8461 CRLF on wire.
install -d -m 0755 "$ROOT/.well-known"
python3 - "$SCRIPT_DIR/policy.txt" "$ROOT/.well-known/mta-sts.txt" <<'PY'
import os, sys
from pathlib import Path
source, destination = map(Path, sys.argv[1:])
data = source.read_text()
destination.write_bytes(data.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))
os.chmod(destination, 0o644)
PY

# Back up an existing *managed* site for explicit rollback. Pre-existing sites
# belonging to other services are never modified.
BACKUP="$(mktemp)"
OLD_SITE=0
if [ -f "$SITE" ]; then
  cp -a "$SITE" "$BACKUP"
  OLD_SITE=1
fi
OLD_LINK=0
[ -L "$LINK" ] && OLD_LINK=1
ARMED=1
rollback() {
  rc=$?
  trap - ERR
  if [ "$ARMED" = 1 ] && [ "$rc" -ne 0 ]; then
    echo "MTA-STS rollback: restoring previous nginx site" >&2
    if [ "$OLD_SITE" = 1 ]; then cp -a "$BACKUP" "$SITE"; else rm -f "$SITE"; fi
    if [ "$OLD_LINK" = 0 ]; then rm -f "$LINK"; fi
    nginx -t && systemctl reload nginx || echo "Nginx rollback needs manual review" >&2
  fi
  rm -f "$BACKUP"
  exit "$rc"
}
trap rollback EXIT
trap 'false' ERR

write_http_site() {
  cat > "$SITE" <<EOF
# Managed by MateMail P4-C: $DOMAIN
# HTTP-01 bootstrap; HTTPS policy is installed only after an authenticated cert.
server {
    listen 80;
    server_name $HOST;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/html;
        allow all;
    }
    location / { return 404; }
}
EOF
  ln -sfn "$SITE" "$LINK"
  nginx -t
  systemctl reload nginx
}

if [ "$ACTION" = prepare ]; then
  # Never downgrade an already active HTTPS site to an HTTP bootstrap.
  if [ -f "$SITE" ] && grep -Fq 'listen 443 ssl;' "$SITE"; then
    echo "Already active: HTTPS policy vhost preserved"
    ARMED=0
    exit 0
  fi
  write_http_site
  ARMED=0
  echo "PREPARED HTTP challenge for $HOST (policy not advertised in DNS)"
  exit 0
fi

# A valid public A record is mandatory for HTTP-01 and real HTTPS policy.
if ! dig @1.1.1.1 +short +tries=1 +time=5 A "$HOST" | grep -Fxq "$SERVER_IP"; then
  echo "DNS action required: $HOST A $SERVER_IP (DNS-only, no proxy)" >&2
  exit 4
fi

if [ "$DOMAIN" = matedesk.pro ]; then
  # Current renewable cert includes *.matedesk.pro; no new issuance needed.
  CERT="matedesk.pro"
else
  CERT="$HOST"
  if [ ! -s "/etc/letsencrypt/live/$CERT/fullchain.pem" ]; then
    if [ ! -e "$SITE" ]; then
      write_http_site
    elif grep -Fq 'listen 443 ssl;' "$SITE"; then
      echo "HTTPS vhost unexpectedly references an absent certificate" >&2
      exit 3
    fi
    certbot certonly --non-interactive --webroot \
      --webroot-path /var/www/html --cert-name "$CERT" -d "$HOST"
  fi
fi
FULLCHAIN="/etc/letsencrypt/live/$CERT/fullchain.pem"
PRIVKEY="/etc/letsencrypt/live/$CERT/privkey.pem"
[ -s "$FULLCHAIN" ] && [ -s "$PRIVKEY" ]
openssl x509 -in "$FULLCHAIN" -noout -checkend 604800 > /dev/null
openssl x509 -in "$FULLCHAIN" -noout -checkhost "$HOST" | grep -Fq "does match certificate"

# Only exact policy path is served (strict public, no app proxy or file listing).
cat > "$SITE" <<EOF
# Managed by MateMail P4-C: $DOMAIN
server {
    listen 80;
    server_name $HOST;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/html;
        allow all;
    }
    location / { return 404; }
}
server {
    listen 443 ssl;
    http2 on;
    server_name $HOST;
    ssl_certificate $FULLCHAIN;
    ssl_certificate_key $PRIVKEY;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    default_type text/plain;
    add_header X-Content-Type-Options "nosniff" always;
    location = /.well-known/mta-sts.txt {
        root $ROOT;
        types { text/plain txt; }
        add_header Cache-Control "public, max-age=3600" always;
        add_header X-Content-Type-Options "nosniff" always;
    }
    location / { return 404; }
}
EOF
ln -sfn "$SITE" "$LINK"
nginx -t
systemctl reload nginx

# nginx reload is asynchronous: existing wildcard vhost workers may still
# answer the first HTTPS request for this newly added SNI hostname. Retry
# a bounded number of times before declaring failure and rolling back.
# No -k/--insecure; every attempt validates the public certificate and body.
verified=0
for attempt in 1 2 3 4 5 6; do
  if curl --fail --silent --show-error --max-time 10 \
      "https://$HOST/.well-known/mta-sts.txt" \
      | python3 "$SCRIPT_DIR/validate.py" - "$DOMAIN"; then
    verified=1
    break
  fi
  sleep 2
done
[ "$verified" = 1 ]
ARMED=0
echo "HTTPS MTA-STS ACTIVE FOR $DOMAIN (testing mode; no DNS TXT published)"
