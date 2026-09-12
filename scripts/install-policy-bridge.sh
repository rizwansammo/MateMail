#!/usr/bin/env bash
# MateMail — install the SMTP policy integration into the Mail Engine.
#
# Puts three things in place on the engine host:
#
#   1. the policy bridge script          data/conf/matemail-gateway/postfix_policy_bridge.py
#   2. the Postfix restriction override  data/conf/postfix/extra.cf
#   3. the bridge sidecar service        docker-compose.override.yml  (already vendored)
#
# It does NOT open any port, touch UFW, or alter DNS.
#
# ── What this changes about mail flow ────────────────────────────────────────
#
# Before: Postfix decides alone. MateMail's policy is computed and never asked
# for. After: Postfix asks MateMail on every authenticated submission and every
# inbound recipient, and a MateMail outage DEFERS mail rather than passing it.
#
# That second half is the risk worth naming out loud: once this is installed,
# MateMail being down means mail stops flowing (temporarily, retryably) rather
# than flowing unchecked. That is the intended trade — an unmetered send path is
# what the policy exists to prevent — but it makes MateMail's availability part
# of the mail path. Install it when you can watch it.
#
# Re-run after any mailcow update that regenerates extra.cf.
#
# Usage:
#   sudo bash scripts/install-policy-bridge.sh              # install
#   sudo bash scripts/install-policy-bridge.sh --check      # verify only, change nothing
set -euo pipefail

MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

BRIDGE_SRC="$REPO_DIR/scripts/postfix_policy_bridge.py"
EXTRA_SRC="$REPO_DIR/deploy/engine/postfix-extra.cf"
OVERRIDE_SRC="$REPO_DIR/deploy/engine/docker-compose.override.yml"

BRIDGE_DST="$MAILCOW_DIR/data/conf/matemail-gateway/postfix_policy_bridge.py"
EXTRA_DST="$MAILCOW_DIR/data/conf/postfix/extra.cf"
OVERRIDE_DST="$MAILCOW_DIR/docker-compose.override.yml"

fail() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "  $*"; }

[[ "$(id -u)" == "0" ]] || fail "must run as root"
[[ -d "$MAILCOW_DIR" ]] || fail "mailcow not found at $MAILCOW_DIR (set MAILCOW_DIR)"
for f in "$BRIDGE_SRC" "$EXTRA_SRC" "$OVERRIDE_SRC"; do
    [[ -f "$f" ]] || fail "missing repository asset: $f"
done

echo "══ MateMail policy bridge ══"
echo "  mailcow: $MAILCOW_DIR"
echo "  mode:    $([[ $CHECK_ONLY == 1 ]] && echo "check only" || echo "install")"
echo

# ── Preflight ────────────────────────────────────────────────────────────────
#
# The secret is what lets the bridge talk to MateMail at all. Without it the
# bridge starts, is asked, cannot authenticate, and fails closed — which means
# every message defers. Better to refuse to install than to install a mail stop.
if ! grep -q "^MATEMAIL_INTERNAL_API_SECRET=" "$MAILCOW_DIR/mailcow.conf" 2>/dev/null; then
    fail "MATEMAIL_INTERNAL_API_SECRET is not set in $MAILCOW_DIR/mailcow.conf.
  It must match INTERNAL_API_SECRET in MateMail's production .env, or the
  bridge will fail closed and defer all mail. Add it, then re-run."
fi

# The engine link must already exist and be internal — the bridge reaches
# MateMail across it. Same invariant the deployment workflow enforces.
if ! docker network inspect matemail_engine_link >/dev/null 2>&1; then
    fail "docker network matemail_engine_link does not exist.
  Create it first:  docker network create --internal matemail_engine_link"
fi
if [[ "$(docker network inspect matemail_engine_link --format '{{.Internal}}')" != "true" ]]; then
    fail "matemail_engine_link is not internal. Refusing to install: the bridge
  would be reachable off-host. Recreate it with --internal."
fi
note "preflight OK — secret present, engine link internal"

# ── The address in the Postfix config must match the address Compose assigns ──
#
# These are two files that have to agree about one number. If they drift,
# Postfix dials an address nothing answers on and every message defers, with
# nothing in either file looking wrong on its own.
CFG_ADDR="$(grep -oE 'check_policy_service inet:[0-9.]+:[0-9]+' "$EXTRA_SRC" | head -1 | sed 's/.*inet://')"
COMPOSE_ADDR="$(grep -A2 'matemail-policy-bridge:' -n "$OVERRIDE_SRC" >/dev/null 2>&1; \
    awk '/ipv4_address: 10.244.0.246/{print "10.244.0.246"}' "$OVERRIDE_SRC" | head -1)"
COMPOSE_PORT="$(awk -F'"' '/LISTEN_PORT:/{print $2}' "$OVERRIDE_SRC" | head -1)"
[[ -n "$CFG_ADDR" ]] || fail "no check_policy_service directive found in $EXTRA_SRC"
[[ "$CFG_ADDR" == "${COMPOSE_ADDR}:${COMPOSE_PORT}" ]] || \
    fail "address mismatch: Postfix dials $CFG_ADDR but Compose binds ${COMPOSE_ADDR}:${COMPOSE_PORT}"
note "address agreement OK — Postfix dials $CFG_ADDR"

if [[ $CHECK_ONLY == 1 ]]; then
    echo
    echo "Check complete. Nothing was changed."
    exit 0
fi

# ── Install ──────────────────────────────────────────────────────────────────
#
# Every destination is backed up with a timestamp before it is replaced, so a
# rollback is a copy rather than a reconstruction.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
install_file() {
    local src="$1" dst="$2"
    mkdir -p "$(dirname "$dst")"
    if [[ -f "$dst" ]]; then
        cp -a "$dst" "$dst.pre-matemail.$STAMP"
        note "backed up $(basename "$dst") -> $(basename "$dst").pre-matemail.$STAMP"
    fi
    cp "$src" "$dst"
    note "installed $dst"
}

install_file "$BRIDGE_SRC" "$BRIDGE_DST"
install_file "$EXTRA_SRC" "$EXTRA_DST"

if ! cmp -s "$OVERRIDE_SRC" "$OVERRIDE_DST"; then
    install_file "$OVERRIDE_SRC" "$OVERRIDE_DST"
else
    note "docker-compose.override.yml already current"
fi

# ── Apply ────────────────────────────────────────────────────────────────────
#
# The bridge comes up BEFORE Postfix is reloaded. Reloading Postfix first would
# open a window where the restriction chain dials a policy service that is not
# yet listening, and every message in that window defers.
cd "$MAILCOW_DIR"
echo
note "starting the policy bridge"
docker compose up -d matemail-policy-bridge

note "waiting for it to accept connections"
for _ in $(seq 1 30); do
    if docker compose exec -T matemail-policy-bridge nc -z 127.0.0.1 "$COMPOSE_PORT" 2>/dev/null; then
        note "bridge is listening"
        break
    fi
    sleep 1
done

note "reloading Postfix so it picks up extra.cf"
docker compose restart postfix-mailcow

echo
echo "── Verify ──"
echo "  effective chain:"
docker compose exec -T postfix-mailcow postconf smtpd_recipient_restrictions smtpd_end_of_data_restrictions
echo
echo "  the policy service must appear BEFORE permit_sasl_authenticated above."
echo "  If it does not, mail is not being checked — stop and investigate."
