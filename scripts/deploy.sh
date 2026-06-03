#!/usr/bin/env bash
# MateMail — Production deployment script
#
# Deploys or updates the MateMail stack on the VPS.
# Run from the project root after cloning and configuring .env.
#
# Full first-time deployment order:
#   1. Configure .env               cp .env.example .env && nano .env
#   2. Obtain TLS certs             ./scripts/init-letsencrypt.sh
#   3. Deploy                       ./scripts/deploy.sh
#   4. Apply mailcow config         sudo ./scripts/apply-mailcow-config.sh
#   5. Install policy bridge        sudo ./scripts/install-policy-bridge.sh
#   6. Create platform admin user   CREATE_ADMIN=true ADMIN_EMAIL=... ADMIN_PASSWORD=... ./scripts/deploy.sh
#
# Subsequent deploys (code updates):
#   ./scripts/deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE="docker compose -f $PROJECT_DIR/docker-compose.yml -f $PROJECT_DIR/docker-compose.prod.yml"

cd "$PROJECT_DIR"

# ── Options ───────────────────────────────────────────────────────────────────
NO_PULL="${NO_PULL:-false}"
CREATE_ADMIN="${CREATE_ADMIN:-false}"
ADMIN_EMAIL="${ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

echo "══════════════════════════════════════════════════════════════════════"
echo "  MateMail Production Deploy"
echo "══════════════════════════════════════════════════════════════════════"

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    echo "ERROR: .env not found."
    echo "  cp .env.example .env && nano .env"
    exit 1
fi

if ! docker info > /dev/null 2>&1; then
    echo "ERROR: Docker is not running or current user lacks Docker access."
    exit 1
fi

# ── Pull latest code ──────────────────────────────────────────────────────────
if [[ "$NO_PULL" != "true" ]]; then
    echo "▸ Pulling latest code..."
    git pull --ff-only
fi

# ── Build images ──────────────────────────────────────────────────────────────
echo "▸ Building Docker images..."
$COMPOSE build

# ── Database migrations ───────────────────────────────────────────────────────
echo "▸ Running database migrations..."
$COMPOSE run --rm backend python manage.py migrate --noinput

# ── Static files ──────────────────────────────────────────────────────────────
echo "▸ Collecting static files..."
$COMPOSE run --rm backend python manage.py collectstatic --noinput --clear

# ── Optional: create platform admin user (first deploy only) ─────────────────
if [[ "$CREATE_ADMIN" == "true" ]]; then
    if [[ -z "$ADMIN_EMAIL" || -z "$ADMIN_PASSWORD" ]]; then
        echo "ERROR: CREATE_ADMIN=true requires ADMIN_EMAIL and ADMIN_PASSWORD."
        exit 1
    fi
    echo "▸ Creating platform admin user ($ADMIN_EMAIL)..."
    $COMPOSE run --rm backend python manage.py shell -c "
from apps.accounts.models import User
email = '$ADMIN_EMAIL'
if not User.objects.filter(email=email).exists():
    u = User.objects.create_superuser(email=email, password='$ADMIN_PASSWORD', full_name='Platform Admin')
    u.is_platform_admin = True
    u.save()
    print(f'Created: {email}')
else:
    print(f'Already exists: {email}')
"
fi

# ── Start / restart services ──────────────────────────────────────────────────
echo "▸ Starting services..."
$COMPOSE up -d --remove-orphans

# ── Health check ──────────────────────────────────────────────────────────────
echo "▸ Waiting for backend health check..."
for i in $(seq 1 24); do
    if $COMPOSE exec -T backend curl -sf http://localhost:8000/api/health/ > /dev/null 2>&1; then
        echo "  Backend healthy ✓"
        break
    fi
    if [[ $i -eq 24 ]]; then
        echo "  ERROR: Backend did not become healthy. Showing last 50 lines of logs:"
        $COMPOSE logs --tail=50 backend
        exit 1
    fi
    echo "  Waiting... ($i/24)"
    sleep 5
done

# ── Reload nginx (zero-downtime config reload) ────────────────────────────────
echo "▸ Reloading nginx..."
$COMPOSE exec -T nginx nginx -s reload || echo "  (nginx reload skipped — may not be running yet)"

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  Deployment complete"
echo "══════════════════════════════════════════════════════════════════════"
echo ""
echo "  App     : https://app.matemail.online"
echo "  Admin   : https://app.matemail.online/admin"
echo "  Health  : https://app.matemail.online/api/health/"
echo ""
echo "  Logs    : $COMPOSE logs -f"
echo "  Status  : $COMPOSE ps"
echo ""
