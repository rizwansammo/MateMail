#!/usr/bin/env bash
# Install the Phase 3 custom-host edge worker on MateServer.
#
# Default: install/update files and the purpose-specific secret, but do not
# start a previously-disabled timer.
#
# --activate: recreate the MateMail backend with the secret, enable the timer,
# and run one provisioning poll immediately.
set -Eeuo pipefail

if [ "${EUID}" -ne 0 ]; then
  echo "ERROR: run as root." >&2
  exit 1
fi

ACTIVATE=0
case "${1:-}" in
  "") ;;
  --activate) ACTIVATE=1 ;;
  *)
    echo "Usage: $0 [--activate]" >&2
    exit 2
    ;;
esac

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR=/opt/MateMail
ENV_FILE="$DEPLOY_DIR/.env"
RUNTIME_DIR=/etc/matemail
RUNTIME_ENV="$RUNTIME_DIR/custom-host-provisioner.env"
WORKER=/usr/local/libexec/matemail-custom-host-provisioner
HOOK=/etc/letsencrypt/renewal-hooks/deploy/matemail-custom-host-nginx.sh
SERVICE=/etc/systemd/system/matemail-custom-host-provisioner.service
TIMER=/etc/systemd/system/matemail-custom-host-provisioner.timer

for bin in python3 nginx certbot systemctl docker install curl; do
  command -v "$bin" >/dev/null 2>&1 || {
    echo "ERROR: required command '$bin' is missing." >&2
    exit 1
  }
done

[ -f "$ENV_FILE" ] || {
  echo "ERROR: $ENV_FILE is missing." >&2
  exit 1
}
[ -f "$DEPLOY_DIR/docker-compose.yml" ] || {
  echo "ERROR: $DEPLOY_DIR/docker-compose.yml is missing." >&2
  exit 1
}

/usr/sbin/nginx -t

install -d -o root -g root -m 0755 /usr/local/libexec
install -d -o root -g root -m 0700 "$RUNTIME_DIR"
install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy

# Generate the purpose-specific secret once if production does not have it yet.
# The value is never printed and the .env mode is preserved as 0600.
SECRET="$(
  ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os
import secrets
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
raw = path.read_text(encoding="utf-8")
lines = raw.splitlines()
key = "CUSTOM_HOST_PROVISIONER_SECRET"
value = ""
found = False

for line in lines:
    if line.startswith(key + "="):
        found = True
        value = line.split("=", 1)[1].strip()
        break

if not value:
    value = secrets.token_urlsafe(48)
    replaced = False
    out = []
    for line in lines:
        if line.startswith(key + "="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1] != "":
            out.append("")
        out.append("# MateMail custom-host edge provisioner")
        out.append(f"{key}={value}")

    tmp = path.with_name(path.name + ".custom-host.tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)

print(value)
PY
)"

[ -n "$SECRET" ] || {
  echo "ERROR: failed to establish CUSTOM_HOST_PROVISIONER_SECRET." >&2
  exit 1
}

install -o root -g root -m 0755 "$SCRIPT_DIR/provisioner.py" "$WORKER"
install -o root -g root -m 0755 "$SCRIPT_DIR/renew-hook.sh" "$HOOK"
install -o root -g root -m 0644   "$SCRIPT_DIR/systemd/matemail-custom-host-provisioner.service" "$SERVICE"
install -o root -g root -m 0644   "$SCRIPT_DIR/systemd/matemail-custom-host-provisioner.timer" "$TIMER"

umask 077
cat > "$RUNTIME_ENV.tmp" <<EOF
CUSTOM_HOST_PROVISIONER_SECRET=$SECRET
MATEMAIL_CUSTOM_HOST_API=http://127.0.0.1:8020/api/internal/custom-hostnames
MATEMAIL_CUSTOM_HOST_ACME_WEBROOT=/var/www/html
EOF
chown root:root "$RUNTIME_ENV.tmp"
chmod 0600 "$RUNTIME_ENV.tmp"
mv -f "$RUNTIME_ENV.tmp" "$RUNTIME_ENV"

systemctl daemon-reload

# Syntax/bytecode checks before anything can be enabled.
python3 -m py_compile "$WORKER"
sh -n "$HOOK"

echo "Custom-host worker files installed."
echo "Secret synchronized without printing it."

if [ "$ACTIVATE" -eq 1 ]; then
  cd "$DEPLOY_DIR"

  # Compose already passed this variable through to backend in Phase 2. Recreate
  # only backend so it receives the newly generated secret; do not restart the
  # mail engine or unrelated MateServer applications.
  docker compose --env-file "$ENV_FILE" config --quiet
  docker compose up -d --no-deps backend

  echo "Waiting for MateMail backend..."
  for i in $(seq 1 30); do
    if curl -fsS --max-time 5 http://127.0.0.1:8020/api/health/ >/dev/null 2>&1; then
      break
    fi
    if [ "$i" -eq 30 ]; then
      echo "ERROR: backend did not become healthy; timer was not enabled." >&2
      exit 1
    fi
    sleep 2
  done

  systemctl enable --now matemail-custom-host-provisioner.timer
  # One immediate run proves the credential/API contract. With no pending
  # hostname it exits cleanly and changes no nginx site.
  systemctl start matemail-custom-host-provisioner.service
  systemctl is-active --quiet matemail-custom-host-provisioner.timer

  echo "Custom-host provisioning timer enabled."
else
  echo "Timer not newly activated. Re-run with --activate after the matching"
  echo "MateMail backend release is deployed."
fi
