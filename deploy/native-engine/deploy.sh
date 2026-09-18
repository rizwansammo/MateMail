#!/usr/bin/env bash
# MateMail Native Engine — deployment.
#
# WHY THIS SCRIPT EXISTS
#   `docker compose up -d` does not recreate a container when only a
#   BIND-MOUNTED CONFIGURATION FILE changed. Compose compares the image, the
#   environment, the mounts and the labels; the *contents* of a mounted file are
#   invisible to it. The file on disk is new, the running process still has the
#   old one, and the deployment looks entirely successful.
#
#   NE3 hit this for real: Rspamd ran for five days with the previous rules
#   after its `local.d` had been updated, and the antivirus scores that were
#   supposed to reject malware were simply not loaded. It was found by checking,
#   not by anything failing.
#
#   Telling operators to "remember to restart Rspamd" is not a fix. This script
#   hashes each service's configuration and passes the hash in as an environment
#   variable, so a configuration change becomes a change Compose CAN see, and
#   the container is recreated exactly when its configuration differs.
#
# USAGE
#   ./deploy.sh              bring the stack up with current configuration
#   ./deploy.sh pull         pull pinned images first, then up
#
# It is safe to run repeatedly: unchanged services are left alone, which is the
# whole point of hashing rather than restarting everything.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "deploy: .env is missing. It holds the engine's secrets and is never" >&2
    echo "        committed; see .env.example for the required keys." >&2
    exit 78
fi

# ── Configuration hashes ─────────────────────────────────────────────────────
#
# One hash per service that mounts configuration. Sorted so the value depends on
# content only, never on directory order, and computed over file CONTENT plus
# the path, so renaming a file is a change too.
config_hash() {
    # shellcheck disable=SC2044
    find "$@" -type f -print0 2>/dev/null \
        | sort -z \
        | xargs -0 sha256sum 2>/dev/null \
        | sha256sum \
        | cut -c1-16
}

NATIVE_RSPAMD_CONFIG_HASH=$(config_hash rspamd)
NATIVE_POSTFIX_CONFIG_HASH=$(config_hash postfix)
NATIVE_DOVECOT_CONFIG_HASH=$(config_hash dovecot)
NATIVE_UNBOUND_CONFIG_HASH=$(config_hash unbound)
export NATIVE_RSPAMD_CONFIG_HASH NATIVE_POSTFIX_CONFIG_HASH \
       NATIVE_DOVECOT_CONFIG_HASH NATIVE_UNBOUND_CONFIG_HASH

echo "deploy: configuration hashes"
echo "  rspamd  $NATIVE_RSPAMD_CONFIG_HASH"
echo "  postfix $NATIVE_POSTFIX_CONFIG_HASH"
echo "  dovecot $NATIVE_DOVECOT_CONFIG_HASH"
echo "  unbound $NATIVE_UNBOUND_CONFIG_HASH"

if [ "${1:-}" = "pull" ]; then
    echo "deploy: pulling pinned images"
    docker compose pull
fi

# Never `down`: that would stop the whole engine, including components whose
# configuration and image are unchanged.
docker compose up -d

echo "deploy: done"
docker compose ps --format '{{.Name}}\t{{.Status}}'
