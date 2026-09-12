#!/usr/bin/env bash
# MateMail — Apply required mailcow configuration changes
#
# Applies the 3 changes that prevent mailcow internal tool names from leaking
# in SMTP/IMAP banners presented to MateMail customers.
#
# CRITICAL: Run this before go-live. Must re-run after any mailcow update
# that resets extra.cf or extra.conf files.
#
# Changes applied:
#   1. MAILCOW_HOSTNAME=mx.matemail.online  (mailcow.conf)
#   2. smtpd_banner = mx.matemail.online ESMTP  (Postfix extra.cf)
#   3. login_greeting = MateMail IMAP ready  (Dovecot extra.conf)
#
# Usage:
#   sudo bash scripts/apply-mailcow-config.sh
#   sudo MAILCOW_DIR=/custom/path bash scripts/apply-mailcow-config.sh

set -euo pipefail

MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"

if [[ "$(id -u)" != "0" ]]; then
    echo "ERROR: This script must be run as root (sudo)."
    exit 1
fi

if [[ ! -d "$MAILCOW_DIR" ]]; then
    echo "ERROR: mailcow not found at $MAILCOW_DIR"
    echo "  Set MAILCOW_DIR if installed elsewhere, e.g.:"
    echo "  sudo MAILCOW_DIR=/home/user/mailcow-dockerized bash scripts/apply-mailcow-config.sh"
    exit 1
fi

echo "══════════════════════════════════════════════════════════════════════"
echo "  MateMail — Apply mailcow configuration"
echo "══════════════════════════════════════════════════════════════════════"
echo "  mailcow directory: $MAILCOW_DIR"
echo ""

# ── 1. MAILCOW_HOSTNAME in mailcow.conf ───────────────────────────────────────
MAILCOW_CONF="$MAILCOW_DIR/mailcow.conf"
echo "▸ [1/3] Setting MAILCOW_HOSTNAME=mx.matemail.online in mailcow.conf..."
if [[ ! -f "$MAILCOW_CONF" ]]; then
    echo "  ERROR: $MAILCOW_CONF not found."
    exit 1
fi

if grep -q "^MAILCOW_HOSTNAME=" "$MAILCOW_CONF"; then
    CURRENT=$(grep "^MAILCOW_HOSTNAME=" "$MAILCOW_CONF" | cut -d= -f2)
    if [[ "$CURRENT" == "mx.matemail.online" ]]; then
        echo "  Already set. Skipping."
    else
        sed -i "s/^MAILCOW_HOSTNAME=.*/MAILCOW_HOSTNAME=mx.matemail.online/" "$MAILCOW_CONF"
        echo "  Updated: $CURRENT → mx.matemail.online"
    fi
else
    echo "MAILCOW_HOSTNAME=mx.matemail.online" >> "$MAILCOW_CONF"
    echo "  Added MAILCOW_HOSTNAME=mx.matemail.online"
fi

# ── 2. smtpd_banner in Postfix extra.cf ──────────────────────────────────────
POSTFIX_EXTRA="$MAILCOW_DIR/data/conf/postfix/extra.cf"
echo "▸ [2/3] Setting smtpd_banner in Postfix extra.cf..."
mkdir -p "$(dirname "$POSTFIX_EXTRA")"

if [[ -f "$POSTFIX_EXTRA" ]] && grep -q "^smtpd_banner" "$POSTFIX_EXTRA"; then
    sed -i "s/^smtpd_banner.*/smtpd_banner = mx.matemail.online ESMTP/" "$POSTFIX_EXTRA"
    echo "  Updated smtpd_banner."
else
    echo "smtpd_banner = mx.matemail.online ESMTP" >> "$POSTFIX_EXTRA"
    echo "  Added smtpd_banner = mx.matemail.online ESMTP"
fi

# ── 3. login_greeting in Dovecot extra.conf ──────────────────────────────────
DOVECOT_EXTRA="$MAILCOW_DIR/data/conf/dovecot/extra.conf"
echo "▸ [3/3] Setting login_greeting in Dovecot extra.conf..."
mkdir -p "$(dirname "$DOVECOT_EXTRA")"

if [[ -f "$DOVECOT_EXTRA" ]] && grep -q "^login_greeting" "$DOVECOT_EXTRA"; then
    sed -i "s/^login_greeting.*/login_greeting = MateMail IMAP ready/" "$DOVECOT_EXTRA"
    echo "  Updated login_greeting."
else
    echo "login_greeting = MateMail IMAP ready" >> "$DOVECOT_EXTRA"
    echo "  Added login_greeting = MateMail IMAP ready"
fi

# ── 4. Restart affected mailcow containers ────────────────────────────────────
echo ""
echo "▸ Restarting postfix-mailcow and dovecot-mailcow..."
cd "$MAILCOW_DIR"
docker compose restart postfix-mailcow dovecot-mailcow

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  Done. All 3 changes applied."
echo "══════════════════════════════════════════════════════════════════════"
echo ""
echo "  ✓ MAILCOW_HOSTNAME = mx.matemail.online"
echo "  ✓ smtpd_banner     = mx.matemail.online ESMTP"
echo "  ✓ login_greeting   = MateMail IMAP ready"
echo ""
echo "Verification:"
echo "  # SMTP banner (should show mx.matemail.online, NOT mailcow or Postfix):"
echo "  telnet mx.matemail.online 25"
echo ""
echo "  # IMAP greeting (should say 'MateMail IMAP ready', NOT Dovecot):"
echo "  openssl s_client -connect imap.matemail.online:993 -quiet 2>/dev/null | head -2"
echo ""
