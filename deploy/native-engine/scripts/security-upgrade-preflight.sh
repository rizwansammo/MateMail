#!/usr/bin/env bash
# MateMail Native Engine — Phase 1/2 read-only production baseline.
#
# Safe to run on MateServer. NO pulls/builds/restarts, writes to running
# configuration, DB changes, volume changes or credential disclosure.
# Prints only versions, runtime health, image IDs, Compose validity and queue.
set -euo pipefail

ROOT="${MATEMAIL_NATIVE_DIR:-/opt/MateMail/engine/deploy/native-engine}"
COMPOSE="$ROOT/docker-compose.yml"
ENVFILE="$ROOT/.env"

command -v docker >/dev/null || { echo "ERROR: docker missing" >&2; exit 1; }
test -f "$COMPOSE" && test -f "$ENVFILE" || {
  echo "ERROR: expected native Compose/.env not found" >&2; exit 1;
}

echo "=== Native Engine upgrade preflight (read-only) ==="
echo "config_path=$COMPOSE"
docker compose --env-file "$ENVFILE" -f "$COMPOSE" config -q
echo "compose=VALID"

# Export no environment variables and echo no secrets.
for engine in dovecot rspamd unbound postfix; do
  container="matemail-native-$engine"
  state="$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}')"
  image="$(docker inspect "$container" --format '{{.Image}}')"
  echo "$engine: health=$state image_id=$image"
  if [[ "$state" != healthy ]]; then
    echo "ERROR: $engine is not healthy" >&2
    exit 1
  fi
done

echo "=== Exact running versions ==="
docker exec matemail-native-dovecot dovecot --version
docker exec matemail-native-rspamd rspamd --version | sed -n '1p'
docker exec matemail-native-unbound unbound -V | sed -n '1p'
docker exec matemail-native-postfix postconf mail_version

echo "=== Static config checks (no output containing credentials) ==="
docker exec matemail-native-dovecot doveconf -n >/dev/null
echo "dovecot_config=VALID"
docker exec matemail-native-unbound unbound-checkconf /etc/unbound/unbound.conf >/dev/null
echo "unbound_config=VALID"
docker exec matemail-native-rspamd rspamadm configtest >/dev/null 2>&1
echo "rspamd_config=VALID"

echo "=== Queue (read-only) ==="
docker exec matemail-native-postfix postqueue -p | sed -n '1,8p'

echo "=== Required persistent volumes (names only) ==="
for volume in native_vmail native_vmail_index native_postfix_queue native_dkim native_rspamd native_redis native_unbound; do
  docker volume inspect "matemail_$volume" >/dev/null 2>&1 && echo "volume_ok=matemail_$volume" || {
    echo "ERROR: expected volume missing: matemail_$volume" >&2
    exit 1
  }
done

echo "PREFLIGHT=PASS (not proof that any future upgrade is compatible)"
