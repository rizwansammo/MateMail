#!/bin/sh
# MateMail Native Engine — Postfix entrypoint.
#
# WHY THIS RENDERS CONFIGURATION
#   Postfix's pgsql map files carry the database password, so they cannot live
#   in Git. They are written here from the environment, which Compose fills from
#   the root-only MateServer `.env`.
#
#   The queries themselves are NOT secret and are the security-critical part, so
#   they are spelled out below where they can be reviewed in a diff — only the
#   credential is substituted.
set -eu

SQLDIR=/etc/postfix/sql

if [ -z "${NATIVE_POSTFIX_DB_PASSWORD:-}" ]; then
    echo "entrypoint: NATIVE_POSTFIX_DB_PASSWORD is unset or empty — refusing to" >&2
    echo "            start. Postfix would come up with every lookup failing," >&2
    echo "            which means deferring all mail rather than obviously" >&2
    echo "            breaking." >&2
    exit 78
fi

# A Postfix map file is `name = value` per line, so a newline in the password
# would end the setting and the rest would be parsed as configuration. Same
# reasoning as the Dovecot entrypoint: constrain the alphabet rather than invent
# an escaping scheme for a value whose generation we also control.
case "$NATIVE_POSTFIX_DB_PASSWORD" in
    *[!A-Za-z0-9._~+=/:@-]*)
        echo "entrypoint: NATIVE_POSTFIX_DB_PASSWORD contains a character that" >&2
        echo "            cannot be represented safely in a Postfix map file." >&2
        echo "            Allowed: A-Z a-z 0-9 . _ ~ + = / : @ -" >&2
        exit 78
        ;;
esac

DB_HOST=${NATIVE_DB_HOST:-db}
DB_PORT=${NATIVE_DB_PORT:-5432}
DB_NAME=${NATIVE_DB_NAME:-matemail_engine}
DB_USER=${NATIVE_POSTFIX_DB_USER:-engine_ro_postfix}

mkdir -p "$SQLDIR"

# 0640 root:postfix — smtpd reads these as the postfix user and nothing else on
# the system needs them. umask covers the window before the chmod lands.
umask 077

# `write_map <file> <query>` renders one lookup table.
#
# The connection block is identical in all four; only the query differs. Postfix
# quotes and escapes the lookup key before substituting %s, so a hostile
# envelope address cannot terminate the literal.
write_map() {
    _file="$SQLDIR/$1"
    _query="$2"
    {
        echo "# Generated at container start by images/postfix/entrypoint.sh."
        echo "# NOT in Git and NOT in the image — it holds one secret."
        echo "hosts = $DB_HOST:$DB_PORT"
        echo "user = $DB_USER"
        echo "password = $NATIVE_POSTFIX_DB_PASSWORD"
        echo "dbname = $DB_NAME"
        echo "query = $_query"
    } > "$_file"
    chown root:postfix "$_file"
    chmod 0640 "$_file"
}

# ── Which domains are ours ───────────────────────────────────────────────────
# Only ACTIVE domains. A suspended domain stops being local, so mail for it is
# rejected as a relay attempt rather than accepted and dropped.
write_map virtual_domain.cf \
    "SELECT name FROM postfix_virtual_domain WHERE name='%s'"

# ── Which mailboxes exist ────────────────────────────────────────────────────
# Postfix only needs a non-empty answer. The view already excludes inactive
# mailboxes and mailboxes in suspended domains, so an unknown recipient is
# rejected at RCPT TO rather than accepted and bounced afterwards — a backscatter
# source and a spam-reputation problem.
write_map virtual_mailbox.cf \
    "SELECT address FROM postfix_virtual_mailbox WHERE address='%s'"

# ── Where mail is delivered ──────────────────────────────────────────────────
# Aliases and forwarding, unioned by the view. Ordered so multi-destination
# delivery is stable rather than dependent on the planner.
write_map virtual_alias.cf \
    "SELECT destination FROM postfix_virtual_alias WHERE address='%s' ORDER BY position"

# ── Who may send as what ─────────────────────────────────────────────────────
# THE anti-spoofing lookup, consumed by smtpd_sender_login_maps together with
# reject_sender_login_mismatch. Returns every login permitted to use this
# envelope sender: the mailbox itself, plus any mailbox an internal alias points
# at. External alias destinations and forwarding targets are structurally absent
# from the view — see migration 003.
write_map sender_login.cf \
    "SELECT owner FROM postfix_sender_login WHERE address='%s'"

postfix set-permissions 2>/dev/null || true
postfix check
exec postfix start-fg
