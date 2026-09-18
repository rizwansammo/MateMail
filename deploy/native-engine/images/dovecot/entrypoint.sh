#!/bin/sh
# MateMail Native Engine — Dovecot entrypoint.
#
# WHY THIS EXISTS
#   Dovecot's configuration cannot read a secret from the environment. That was
#   measured, not assumed: `password = %{env:NATIVE_DOVECOT_DB_PASSWORD}` parses
#   happily, `doveconf -n` echoes it back, and at runtime libpq reports
#   "fe_sendauth: no password supplied" — the placeholder reaches PostgreSQL as
#   an empty string. A configuration that looks correct and authenticates nobody
#   is worse than one that fails to parse, so the expansion happens here, before
#   Dovecot starts.
#
#   The alternative — committing the password to dovecot.conf — is not an
#   option: that file is in Git.
#
# SHELL BUILTINS ONLY
#   The upstream image ships no coreutils: no cat, no chown, no chmod, not even
#   tr. (The image Dockerfile relies on the same fact when it borrows a chown
#   from a builder stage.) Everything below is `case`, `echo`, redirection and
#   `exec`, all builtins, so this script depends on nothing that is not there.
#   File mode comes from umask rather than a chmod that does not exist, and the
#   owner is root because that is who runs this.
set -eu

CONF=/etc/dovecot/engine-db.conf

if [ -z "${NATIVE_DOVECOT_DB_PASSWORD:-}" ]; then
    echo "entrypoint: NATIVE_DOVECOT_DB_PASSWORD is unset or empty — refusing to" >&2
    echo "            start. Dovecot would otherwise come up healthy and" >&2
    echo "            authenticate nobody, which reads as a password problem for" >&2
    echo "            days before anyone suspects the configuration." >&2
    exit 78
fi

# A Dovecot config value is not a quoted string: '#' opens a comment and a
# newline ends the setting. A password containing either would truncate the
# credential or terminate the block and leave the rest of the file parsed as
# something else entirely. Rather than invent an escaping scheme for a value we
# also control the generation of, the alphabet is constrained and anything
# outside it is refused. The password is never echoed — only the rejection.
case "$NATIVE_DOVECOT_DB_PASSWORD" in
    *[!A-Za-z0-9._~+=/:@-]*)
        echo "entrypoint: NATIVE_DOVECOT_DB_PASSWORD contains a character that" >&2
        echo "            cannot be represented safely in a Dovecot config value." >&2
        echo "            Allowed: A-Z a-z 0-9 . _ ~ + = / : @ -" >&2
        exit 78
        ;;
esac

# umask before the redirection: the file is created 0600 and is never briefly
# world-readable. root owns it because this script runs as root.
umask 077
{
    echo "# Generated at container start by images/dovecot/entrypoint.sh."
    echo "# NOT in Git and NOT in the image — it exists only in this container's"
    echo "# writable layer, and it holds one secret."
    echo "pgsql engine {"
    echo "  parameters {"
    echo "    host     = ${NATIVE_DB_HOST:-db}"
    echo "    port     = ${NATIVE_DB_PORT:-5432}"
    echo "    dbname   = ${NATIVE_DB_NAME:-matemail_engine}"
    echo "    user     = ${NATIVE_DOVECOT_DB_USER:-engine_ro_dovecot}"
    echo "    password = ${NATIVE_DOVECOT_DB_PASSWORD}"
    echo "    sslmode  = ${NATIVE_DB_SSLMODE:-disable}"
    echo "  }"
    echo "}"
} > "$CONF"

# The auth-policy hook tells the engine API about a successful login, because
# Dovecot cannot write the database itself. Its credential is separate from the
# provisioning secret and opens exactly one endpoint.
#
# Optional: if no policy secret is configured the hook is simply not enabled,
# and Dovecot runs without last-login reporting rather than failing to start.
# Authentication does not depend on it.
if [ -n "${NATIVE_DOVECOT_POLICY_SECRET:-}" ]; then
    {
        echo "# Generated at container start — holds one secret."
        echo "auth_policy_server_url = ${NATIVE_POLICY_URL:-http://api:8451/v1/dovecot/policy/}"
        echo "auth_policy_server_api_header = X-Native-Policy-Secret: ${NATIVE_DOVECOT_POLICY_SECRET}"
        echo "auth_policy_hash_nonce = ${NATIVE_POLICY_NONCE:-matemail-native-engine}"
        # Report only. Dovecot must not ask this service for PERMISSION to log a
        # customer in: that would put mail access behind the availability of a
        # bookkeeping endpoint. It reports after the fact and moves on.
        echo "auth_policy_check_before_auth = no"
        echo "auth_policy_check_after_auth = no"
        echo "auth_policy_report_after_auth = yes"
        echo "auth_policy_reject_on_fail = no"
    } > /etc/dovecot/engine-policy.conf
else
    echo "entrypoint: NATIVE_DOVECOT_POLICY_SECRET unset — last-login reporting disabled" >&2
    echo "# No policy secret configured; last-login reporting is disabled."         > /etc/dovecot/engine-policy.conf
fi

# The doveadm HTTP API key (NE4). Same reasoning as the database password: it
# cannot come from the environment through the configuration, and the
# configuration is in Git.
#
# Optional, like the policy secret: without it the administrative API is simply
# not usable, and Dovecot still serves mail. An engine that refused to start
# because a reporting credential was missing would be trading a cosmetic outage
# for a real one.
if [ -n "${NATIVE_DOVEADM_API_KEY:-}" ]; then
    {
        echo "# Generated at container start - holds one secret."
        echo "doveadm_api_key = ${NATIVE_DOVEADM_API_KEY}"
    } > /etc/dovecot/engine-doveadm.conf
else
    echo "entrypoint: NATIVE_DOVEADM_API_KEY unset - the doveadm API is disabled" >&2
    echo "# No API key configured; the administrative API is unusable."         > /etc/dovecot/engine-doveadm.conf
fi

# Upstream's entrypoint and command, unchanged.
exec /usr/bin/tini -- /dovecot/sbin/dovecot -F
