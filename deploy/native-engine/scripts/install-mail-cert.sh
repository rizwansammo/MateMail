#!/usr/bin/env bash
# Native Engine — install the mail certificate into the engine's TLS volume.
#
# WHY THIS EXISTS
#   Native Postfix mounts `matemail_native_tls` at /etc/ssl/mail and its
#   submission service is configured `smtpd_tls_security_level=encrypt`, so
#   STARTTLS is mandatory. Before NE6 that volume was EMPTY: the service would
#   have refused every submission attempt, and the failure would have looked
#   like a client problem rather than a missing file.
#
#   Django verifies the certificate and the hostname — its SMTP backend uses
#   ssl.create_default_context() — so a self-signed certificate is not an
#   option either. It has to be the real Let's Encrypt certificate for
#   mx.matemail.pro, managed under the matemail-mail-pro Certbot lineage.
#
# WHY A COPY RATHER THAN A BIND MOUNT
#   Mounting /etc/letsencrypt into the container would hand the mail engine
#   every private key on the machine — MateERP, MateDesk, TalkRoom and the rest
#   all live in that directory. Copying one certificate in gives Postfix
#   exactly what it needs and nothing else. It costs a renewal hook, which is
#   the file next to this one.
#
# IDEMPOTENT
#   Safe to run repeatedly. It only reloads Postfix when the certificate
#   actually changed, so the renewal hook cannot turn into a twice-daily
#   restart of the mail server.
set -euo pipefail
umask 077

HOST="${MAIL_CERT_HOSTNAME:-mx.matemail.pro}"
# Renewal identity is distinct from the hostnames covered by the certificate.
CERT_NAME="${MAIL_CERT_NAME:-matemail-mail-pro}"
MAIL_CERT_EXTRA_HOSTNAMES="${MAIL_CERT_EXTRA_HOSTNAMES-}"
VOLUME="${MAIL_TLS_VOLUME:-matemail_native_tls}"
LIVE="/etc/letsencrypt/live/${CERT_NAME}"
POSTFIX_CONTAINER="${POSTFIX_CONTAINER:-matemail-native-postfix}"
DOVECOT_CONTAINER="${DOVECOT_CONTAINER:-matemail-native-dovecot}"

# Postfix's smtpd runs as uid 100 / gid 102 in this image and reads the key
# itself — the submission service is not chrooted. The key is therefore group
# readable by that gid and nothing wider.
POSTFIX_GID="${POSTFIX_GID:-102}"

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "FAILED: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "must run as root to read the certificate private key"
[ -r "$LIVE/fullchain.pem" ] || die "no certificate at $LIVE/fullchain.pem"
[ -r "$LIVE/privkey.pem" ]   || die "no private key at $LIVE/privkey.pem"

# Confirm the certificate actually covers the name Django will verify. A
# certificate for the wrong host would install cleanly and fail only at the
# first send, with a TLS error that points nowhere useful.
for hostname in "$HOST" ${MAIL_CERT_EXTRA_HOSTNAMES}; do
    openssl x509 -in "$LIVE/fullchain.pem" -noout -checkhost "$hostname" > /dev/null \
        || die "$LIVE/fullchain.pem is not valid for $hostname"
done

MOUNT=$(docker volume inspect "$VOLUME" --format '{{.Mountpoint}}') \
    || die "no such volume: $VOLUME"
[ -d "$MOUNT" ] || die "volume $VOLUME has no mountpoint"

# Compare before writing, so an unchanged certificate is a no-op and Postfix is
# left alone.
changed=0
if ! cmp -s "$LIVE/fullchain.pem" "$MOUNT/cert.pem" 2>/dev/null; then changed=1; fi
if ! cmp -s "$LIVE/privkey.pem"   "$MOUNT/key.pem"  2>/dev/null; then changed=1; fi

if [ "$changed" -eq 0 ]; then
    log "certificate for $HOST is already current; nothing to do"
    exit 0
fi

# Written to temporary names in the SAME directory and renamed, so Postfix can
# never read a half-copied certificate or a key that does not match it.
install -m 0644 -o root -g root "$LIVE/fullchain.pem" "$MOUNT/.cert.pem.new"
install -m 0640 -o root -g "$POSTFIX_GID" "$LIVE/privkey.pem" "$MOUNT/.key.pem.new"
mv -f "$MOUNT/.cert.pem.new" "$MOUNT/cert.pem"
mv -f "$MOUNT/.key.pem.new"  "$MOUNT/key.pem"
sync

log "installed certificate for $HOST into $VOLUME"
log "  subject $(openssl x509 -in "$MOUNT/cert.pem" -noout -subject | cut -d= -f2-)"
log "  expires $(openssl x509 -in "$MOUNT/cert.pem" -noout -enddate | cut -d= -f2)"
log "  fingerprint $(openssl x509 -in "$MOUNT/cert.pem" -noout -fingerprint -sha256 | cut -d= -f2)"

# Reload rather than restart: a reload re-reads the certificate without
# dropping a connection or losing the queue. Absent container, no reload — the
# next start reads the new files anyway.
#
# BOTH services since NE7. Dovecot serves public IMAPS from the same files, and
# reloading only Postfix would leave clients being handed an expired
# certificate for up to ninety days without anything looking wrong on the
# Postfix side.
reload_service() {
    local container="$1" command="$2"
    docker inspect "$container" > /dev/null 2>&1 || return 0
    if docker exec "$container" sh -c "$command" > /dev/null 2>&1; then
        log "reloaded $container"
    else
        log "WARNING: could not reload $container; it will pick the"
        log "         certificate up on its next start"
    fi
}

reload_service "$POSTFIX_CONTAINER" "postfix reload"
reload_service "$DOVECOT_CONTAINER" "doveadm reload"
