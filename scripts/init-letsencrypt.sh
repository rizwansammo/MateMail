#!/usr/bin/env bash
# MateMail — TLS certificate setup via the host nginx
#
# Since this server already runs a shared nginx on ports 80/443,
# we use certbot --nginx to obtain and auto-configure TLS for MateMail's domain.
# The host nginx MUST have the matemail vhost enabled first.
#
# Usage (run on the VPS as root):
#   sudo ./scripts/init-letsencrypt.sh

set -euo pipefail

CERTBOT_EMAIL="${CERTBOT_EMAIL:-joe@netswitch.net}"
DOMAINS=("app.matemail.online" "matemail.online")

echo "══════════════════════════════════════════════════════════════════════"
echo "  MateMail — TLS Certificate Setup"
echo "══════════════════════════════════════════════════════════════════════"
echo "  Domains : ${DOMAINS[*]}"
echo "  Email   : $CERTBOT_EMAIL"
echo ""

if [[ "$(id -u)" != "0" ]]; then
    echo "ERROR: Run as root (sudo)."
    exit 1
fi

# ── Step 1: Install certbot if not present ────────────────────────────────────
if ! command -v certbot &> /dev/null; then
    echo "▸ Installing certbot..."
    apt-get update -q
    apt-get install -y certbot python3-certbot-nginx
fi

# ── Step 2: Ensure the vhost is installed ────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VHOST_SRC="$PROJECT_DIR/nginx/matemail-vhost.conf"
VHOST_DEST="/etc/nginx/sites-available/matemail"
VHOST_LINK="/etc/nginx/sites-enabled/matemail"

if [[ ! -f "$VHOST_DEST" ]]; then
    echo "▸ Installing vhost config..."
    cp "$VHOST_SRC" "$VHOST_DEST"
    ln -sf "$VHOST_DEST" "$VHOST_LINK"
    nginx -t && nginx -s reload
    echo "  Vhost installed and nginx reloaded."
else
    echo "▸ Vhost already installed at $VHOST_DEST"
fi

# ── Step 3: Obtain certificate ────────────────────────────────────────────────
DOMAIN_ARGS=""
for d in "${DOMAINS[@]}"; do
    DOMAIN_ARGS="$DOMAIN_ARGS -d $d"
done

echo "▸ Running certbot..."
certbot --nginx \
    $DOMAIN_ARGS \
    --email "$CERTBOT_EMAIL" \
    --agree-tos \
    --no-eff-email \
    --redirect \
    --keep-until-expiring

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  Done — certificate obtained and nginx updated."
echo "══════════════════════════════════════════════════════════════════════"
echo ""
echo "  Auto-renewal is handled by certbot's systemd timer:"
echo "    systemctl status certbot.timer"
echo ""
echo "  Verify:"
echo "    curl -v https://app.matemail.online/api/health/"
echo ""
