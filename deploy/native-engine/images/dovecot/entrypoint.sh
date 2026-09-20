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
#   Generated root-only files get their mode from umask rather than a chmod that
#   does not exist. The PostBox master passwd-file is the one exception:
#   Dockerfile pre-creates it 0640 root:dovecot so the unprivileged auth worker
#   can read it; shell redirection truncates that existing file without changing
#   its ownership or mode.
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

# The PostBox master credential (P11).
#
# PostBox authenticates a person with the password they type, then keeps
# reading their mailbox for the life of the session. MateMail stores no mailbox
# password, so the alternative is retaining the customer's password wherever
# the session lives. A master identity in one root-only file on one host is the
# smaller exposure.
#
# NOT optional, unlike the two above. dovecot.conf sets
# `auth_master_user_separator` unconditionally and includes this file with
# `!include`. A missing render would leave the separator active with no master
# passdb behind it: PostBox would fail to authenticate, and so would any
# ordinary address that happened to contain the separator, in a way nobody
# would trace back to here. Refusing to start says it once, loudly.
if [ -z "${NATIVE_POSTBOX_MASTER_PASSWORD:-}" ]; then
    echo "entrypoint: NATIVE_POSTBOX_MASTER_PASSWORD is unset or empty - refusing" >&2
    echo "            to start. dovecot.conf enables master-user login and this" >&2
    echo "            is the credential behind it; without it PostBox cannot read" >&2
    echo "            any mailbox and addresses containing '*' would fail oddly." >&2
    exit 78
fi

# Same alphabet rule as the database password, for the same reason: a passwd
# file is colon-separated and line-oriented, so ':' or a newline in the value
# would silently redefine the record. ':' is excluded here even though the
# database password allows it.
case "$NATIVE_POSTBOX_MASTER_PASSWORD" in
    *[!A-Za-z0-9._~+=/@-]*)
        echo "entrypoint: NATIVE_POSTBOX_MASTER_PASSWORD contains a character that" >&2
        echo "            cannot be represented safely in a passwd-file record." >&2
        echo "            Allowed: A-Z a-z 0-9 . _ ~ + = / @ -" >&2
        exit 78
        ;;
esac

# Dockerfile pre-creates this exact path as 0640 root:dovecot. Redirection
# truncates that existing inode and therefore preserves the access the auth
# worker needs. Keep umask 077 as a fail-closed fallback: if the placeholder is
# ever removed from the image, the new file becomes 0600 root and master auth
# fails loudly instead of broadening access to the credential.
umask 077
{
    echo "# Generated at container start by images/dovecot/entrypoint.sh."
    echo "# One record, one secret. Never in Git and never in the image."
    # user:password:uid:gid:gecos:home:shell:extra_fields
    # Only the first two fields matter for a master passdb; the rest stay empty
    # so this record can never be mistaken for a mail account.
    echo "postbox:{PLAIN}${NATIVE_POSTBOX_MASTER_PASSWORD}::::::"
} > /etc/dovecot/postbox-master

{
    echo "# Generated at container start by images/dovecot/entrypoint.sh."
    echo "# The master passdb. The credential itself is in postbox-master."
    echo "passdb passwd-file {"
    echo "  master = yes"
    echo "  passwd_file_path = /etc/dovecot/postbox-master"
    # The record above stores the value in the clear inside a 0600 file that
    # exists only in this container's writable layer. A hash would be better
    # if this were a user database; for a single service credential that the
    # operator rotates by restarting the container, the added moving part
    # buys less than it costs.
    echo "  default_password_scheme = PLAIN"
    echo "}"
} > /etc/dovecot/engine-postbox-master.conf

# Upstream's entrypoint and command, unchanged.
exec /usr/bin/tini -- /dovecot/sbin/dovecot -F
