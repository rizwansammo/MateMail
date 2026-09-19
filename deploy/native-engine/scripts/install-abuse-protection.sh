#!/usr/bin/env bash
# Install abuse protection for the public Native mail listeners.
#
# NE7 opens 25, 587 and 993 to the Internet. Submission and IMAPS will start
# receiving credential-stuffing attempts within hours of the ports opening;
# that is not a prediction, it is what happens to every mail server.
#
# WHAT THIS INSTALLS
#   - a log shipper, because Native logs to `docker logs` and fail2ban watches
#     files (see scripts/mail-log-shipper.sh)
#   - two filters, for Postfix SASL failures and Dovecot login failures
#   - a ban action that writes into DOCKER-USER, because the standard fail2ban
#     action writes into INPUT, which Docker-published traffic never reaches
#   - two jails, and nothing else. Explicitly NOT sshd.
#
# SAFE TO RE-RUN. It refreshes configuration and leaves existing bans alone.
set -euo pipefail
umask 022

SRC="$(cd "$(dirname "$0")/.." && pwd)"
MAILLOG=/var/log/matemail/mail.log

[ "$(id -u)" = 0 ] || { echo "install: must run as root" >&2; exit 1; }

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

log "installing fail2ban"
if ! command -v fail2ban-client > /dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban > /dev/null
fi
fail2ban-client --version 2>/dev/null | head -1 | sed 's/^/  /'

# ── Log shipper ─────────────────────────────────────────────────────────────
log "installing the mail log shipper"
mkdir -p /var/log/matemail
chmod 750 /var/log/matemail
install -m 0755 "$SRC/scripts/mail-log-shipper.sh" \
    /opt/MateMailNative/deploy/native-engine/scripts/mail-log-shipper.sh 2>/dev/null || true
install -m 0644 "$SRC/systemd/matemail-maillog.service" /etc/systemd/system/
install -m 0644 "$SRC/fail2ban/logrotate-matemail-mail" \
    /etc/logrotate.d/matemail-mail
systemctl daemon-reload
systemctl enable --now matemail-maillog.service > /dev/null
log "  shipper: $(systemctl is-active matemail-maillog.service)"

# ── Filters and action ──────────────────────────────────────────────────────
log "installing filters and ban action"
install -m 0644 "$SRC/fail2ban/filter.d/"*.conf /etc/fail2ban/filter.d/
install -m 0644 "$SRC/fail2ban/action.d/"*.conf /etc/fail2ban/action.d/

# ── Jails, with the operator's address protected ────────────────────────────
#
# These jails go live at the same moment the submission port does, and the
# person about to test wrong-password handling is connected over SSH from the
# address that would be banned for it. SSH_CLIENT is the authoritative source
# for that address; it is written into ignoreip so the test cannot lock the
# operator out of their own server.
OPERATOR_IP="${OPERATOR_IP:-}"
if [ -z "$OPERATOR_IP" ] && [ -n "${SSH_CLIENT:-}" ]; then
    OPERATOR_IP="${SSH_CLIENT%% *}"
fi

install -m 0644 "$SRC/fail2ban/jail.local" /etc/fail2ban/jail.local
if [ -n "$OPERATOR_IP" ]; then
    sed -i "s|^ignoreip = .*|ignoreip = 127.0.0.1/8 ::1 ${OPERATOR_IP}|" \
        /etc/fail2ban/jail.local
    log "  operator address added to ignoreip"
else
    log "  WARNING: no operator address detected; ignoreip is loopback only."
    log "           A failed-login test from a workstation could ban it."
fi

# ── Validate before starting ────────────────────────────────────────────────
log "validating configuration"
fail2ban-client -t > /dev/null || { echo "fail2ban configuration is invalid" >&2; exit 1; }
log "  configuration OK"

systemctl enable fail2ban > /dev/null 2>&1 || true
systemctl restart fail2ban
sleep 3

log "active jails"
fail2ban-client status 2>/dev/null | sed 's/^/  /'
log "done"
