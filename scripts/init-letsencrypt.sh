#!/usr/bin/env bash
# MateMail — Let's Encrypt certificate bootstrap
#
# Run this ONCE on first deployment to obtain the initial TLS certificate.
# After successful issuance, switch nginx to the TLS config via docker-compose.prod.yml.
#
# Prerequisites:
#   - Docker and Docker Compose v2 installed
#   - DNS A records pointing matemail.online and app.matemail.online to this server
#   - Ports 80 and 443 open in the firewall
#   - The base docker-compose.yml stack is running (nginx serving HTTP)
#
# Usage:
#   ./scripts/init-letsencrypt.sh                        # production cert
#   STAGING=1 ./scripts/init-letsencrypt.sh              # staging cert (test first!)
#   EXTRA_DOMAINS="webmail.matemail.online" ./scripts/init-letsencrypt.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

CERTBOT_EMAIL="${CERTBOT_EMAIL:-joe@netswitch.net}"
STAGING="${STAGING:-0}"
PRIMARY_DOMAIN="matemail.online"
DEFAULT_DOMAINS=("matemail.online" "app.matemail.online")
EXTRA_DOMAINS_RAW="${EXTRA_DOMAINS:-}"

# Build full domain list
ALL_DOMAINS=("${DEFAULT_DOMAINS[@]}")
if [[ -n "$EXTRA_DOMAINS_RAW" ]]; then
    IFS=',' read -ra EXTRA <<< "$EXTRA_DOMAINS_RAW"
    ALL_DOMAINS+=("${EXTRA[@]}")
fi

echo "══════════════════════════════════════════════════════════════════════"
echo "  MateMail — Let's Encrypt Certificate Bootstrap"
echo "══════════════════════════════════════════════════════════════════════"
echo "  Domains : ${ALL_DOMAINS[*]}"
echo "  Email   : $CERTBOT_EMAIL"
echo "  Staging : $STAGING"
echo ""

cd "$PROJECT_DIR"

if [[ ! -f ".env" ]]; then
    echo "ERROR: .env not found."
    echo "  Run: cp .env.example .env && nano .env"
    exit 1
fi

# Ensure nginx is up (HTTP-only mode, which serves /.well-known/acme-challenge/)
echo "▸ Ensuring nginx is running for ACME challenge..."
docker compose up -d nginx
sleep 3

# Verify nginx is reachable
if ! curl -sf "http://localhost/api/health/" > /dev/null 2>&1; then
    echo "WARNING: Could not reach http://localhost/api/health/ — nginx may not be ready."
    echo "  Continuing anyway, but certbot validation may fail."
fi

# Build certbot arguments
CERTBOT_ARGS=(
    "certonly"
    "--webroot"
    "--webroot-path=/var/www/certbot"
    "--email" "$CERTBOT_EMAIL"
    "--agree-tos"
    "--no-eff-email"
    "--keep-until-expiring"
)

for domain in "${ALL_DOMAINS[@]}"; do
    CERTBOT_ARGS+=("-d" "$domain")
done

if [[ "$STAGING" == "1" ]]; then
    CERTBOT_ARGS+=("--staging")
    echo "  ⚠  STAGING MODE — cert will NOT be trusted by browsers."
    echo "     Run again with STAGING=0 after verifying this works."
    echo ""
fi

echo "▸ Requesting certificate from Let's Encrypt..."
docker run --rm \
    --network "$(basename "$PROJECT_DIR")_matemail_external" \
    -v "$(basename "$PROJECT_DIR")_certbot_certs:/etc/letsencrypt" \
    -v "$(basename "$PROJECT_DIR")_certbot_www:/var/www/certbot" \
    certbot/certbot:latest \
    "${CERTBOT_ARGS[@]}"

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  Certificate obtained successfully!"
echo "══════════════════════════════════════════════════════════════════════"
echo ""
echo "NEXT STEPS"
echo ""
echo "  1. Switch to production TLS configuration:"
echo "     docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
echo ""
echo "  2. Verify HTTPS:"
echo "     curl -v https://app.matemail.online/api/health/"
echo ""
echo "  3. Add auto-renewal to crontab (sudo crontab -e):"
cat <<'CRON'
     # Let's Encrypt renewal (runs daily at noon, renews if <30 days remain)
     0 12 * * * docker run --rm \
         -v matemail_certbot_certs:/etc/letsencrypt \
         -v matemail_certbot_www:/var/www/certbot \
         certbot/certbot:latest renew --quiet && \
         docker compose -f /opt/matemail/docker-compose.yml \
                        -f /opt/matemail/docker-compose.prod.yml \
                        exec nginx nginx -s reload
CRON
echo ""
echo "  4. Apply mailcow config changes:"
echo "     sudo ./scripts/apply-mailcow-config.sh"
echo ""
