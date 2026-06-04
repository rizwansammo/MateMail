#!/usr/bin/env bash
# MateMail — TLS certificate setup via the host nginx
#
# Two-phase approach:
#   1. Install a minimal HTTP vhost so nginx can serve the ACME challenge
#   2. Obtain the cert with certbot (webroot mode — no nginx modification)
#   3. Install the full vhost (now cert files exist, nginx -t passes)
#
# Usage (run on the VPS as root):
#   sudo CERTBOT_EMAIL=you@example.com /opt/matemail/scripts/init-letsencrypt.sh

set -euo pipefail

CERTBOT_EMAIL="${CERTBOT_EMAIL:-mrizwan.sammo@gmail.com}"
DOMAINS=("app.matemail.online" "matemail.online")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VHOST_SRC="$PROJECT_DIR/nginx/matemail-vhost.conf"
VHOST_DEST="/etc/nginx/sites-available/matemail"
VHOST_LINK="/etc/nginx/sites-enabled/matemail"
WEBROOT="/var/www/certbot"
CERT_PATH="/etc/letsencrypt/live/app.matemail.online/fullchain.pem"

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

# ── Step 2: Build certbot domain args ────────────────────────────────────────
DOMAIN_ARGS=""
for d in "${DOMAINS[@]}"; do
    DOMAIN_ARGS="$DOMAIN_ARGS -d $d"
done

# ── Step 3: First-time cert issuance ─────────────────────────────────────────
if [[ ! -f "$CERT_PATH" ]]; then
    echo "▸ No certificate found — starting first-time issuance..."

    # Install a minimal HTTP-only vhost so nginx can serve the ACME challenge.
    # The full vhost (with SSL) is installed AFTER certs exist.
    mkdir -p "$WEBROOT"
    cat > "$VHOST_DEST" << 'NGINXEOF'
server {
    listen 80;
    server_name matemail.online app.matemail.online;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://app.matemail.online$request_uri; }
}
NGINXEOF
    ln -sf "$VHOST_DEST" "$VHOST_LINK"
    nginx -t && nginx -s reload
    echo "  Temporary HTTP vhost active."

    echo "▸ Running certbot (webroot mode)..."
    certbot certonly --webroot -w "$WEBROOT" \
        $DOMAIN_ARGS \
        --email "$CERTBOT_EMAIL" \
        --agree-tos \
        --no-eff-email

    echo "▸ Installing full vhost (certs now exist)..."
    cp "$VHOST_SRC" "$VHOST_DEST"
    nginx -t && nginx -s reload
    echo "  Full vhost installed."

else
    # Re-run / renewal path
    echo "▸ Certificate already exists — renewing if needed..."
    certbot renew --quiet

    if [[ ! -f "$VHOST_DEST" ]] || ! grep -q "ssl_certificate" "$VHOST_DEST" 2>/dev/null; then
        echo "▸ Installing full vhost..."
        cp "$VHOST_SRC" "$VHOST_DEST"
        ln -sf "$VHOST_DEST" "$VHOST_LINK"
        nginx -t && nginx -s reload
        echo "  Full vhost installed."
    else
        echo "▸ Vhost already installed — reloading nginx..."
        nginx -t && nginx -s reload
    fi
fi

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  Done — MateMail is live!"
echo "══════════════════════════════════════════════════════════════════════"
echo ""
echo "  Auto-renewal is handled by certbot's systemd timer:"
echo "    systemctl status certbot.timer"
echo ""
echo "  Verify:"
echo "    curl -v https://app.matemail.online/api/health/"
echo ""
