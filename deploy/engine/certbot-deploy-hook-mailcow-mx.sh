#!/bin/bash
#
# Certbot deploy hook - MateMail Mail Engine (mailcow) certificate.
#
# WHY THIS FILE EXISTS
#   The Mail Engine runs with SKIP_LETS_ENCRYPT=y: it never obtains or renews
#   its own certificate, because host-native Certbot is the single TLS
#   authority on MateServer, and two ACME clients competing for port 80 is a
#   renewal failure waiting to happen. Certbot therefore owns the certificate,
#   and something has to carry each renewal into the engine. This is that
#   something. Without it the engine would keep serving the certificate that
#   was current on the day it started, and would begin serving an expired one
#   roughly 90 days later.
#
# WHICH CERTIFICATE IT MANAGES
#   Only mx.matemail.online - the Mail Engine's canonical hostname, presented
#   on SMTP submission (STARTTLS), IMAP/POP3 and the engine's private HTTPS
#   interface. Every other certificate on this host belongs to a different
#   NetaMate application. The RENEWED_LINEAGE guard below makes this hook a
#   silent no-op for all of them, so renewing an unrelated certificate can
#   never restart mail services.
#
# WHY ONLY THREE CONTAINERS ARE RESTARTED
#   postfix-mailcow, dovecot-mailcow and nginx-mailcow are the only services
#   that hold this certificate open. Restarting the whole mailcow stack would
#   take MariaDB, Redis, Rspamd, ClamAV and SOGo down as well, turning a file
#   copy into a mail outage. Nothing outside mailcow is touched: no other
#   NetaMate application on MateServer is restarted, reloaded or signalled.
#
# WHY COPY RATHER THAN SYMLINK
#   mailcow bind-mounts data/assets/ssl into the containers. A host symlink
#   pointing into /etc/letsencrypt/archive resolves to a path that does not
#   exist inside the container, so the services would start with no usable
#   certificate. The pinned upstream documentation says to copy; this copies.
#
# Never prints or logs private-key material.
#
set -euo pipefail

CERT_NAME="mx.matemail.online"
MAILCOW_DIR="/opt/mailcow-dockerized"
SSL_DIR="${MAILCOW_DIR}/data/assets/ssl"

# Certbot exports RENEWED_LINEAGE for the lineage it just renewed. Do nothing
# unless that lineage is ours.
if [ "${RENEWED_LINEAGE:-}" != "/etc/letsencrypt/live/${CERT_NAME}" ]; then
  exit 0
fi

logger -t mailcow-cert-hook "deploying renewed ${CERT_NAME} certificate to the Mail Engine"

install -o root -g root -m 644 "${RENEWED_LINEAGE}/fullchain.pem" "${SSL_DIR}/cert.pem"
install -o root -g root -m 600 "${RENEWED_LINEAGE}/privkey.pem"   "${SSL_DIR}/key.pem"

cd "${MAILCOW_DIR}"
for svc in postfix-mailcow dovecot-mailcow nginx-mailcow; do
  if docker compose restart "${svc}" >/dev/null 2>&1; then
    logger -t mailcow-cert-hook "restarted ${svc}"
  else
    logger -t mailcow-cert-hook "WARNING: could not restart ${svc}"
  fi
done

logger -t mailcow-cert-hook "certificate deployment complete"
