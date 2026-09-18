# MateMail Native Engine — Postfix image.
#
# WHAT THIS CONFIGURES
#   Postfix with the PostgreSQL map driver, which is what lets it answer
#   "is this domain ours / does this mailbox exist / may this login use this
#   sender" from the engine database.
#
# WHY IT IS SHAPED THIS WAY
#   There is no official Postfix image, and Step 8 forbids solving that with a
#   random community one for a component this critical. Debian stable is chosen
#   over Alpine because Postfix's PostgreSQL and SASL integration is far better
#   exercised there, and because the security update feed is the one most mail
#   operators actually run.
#
#   Built by GitHub Actions and published to GHCR — never built on MateServer.
#
# WHICH PHASE OWNS IT
#   NE1 defines the image. NE3 supplies the real maps and mail flow.

FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         postfix postfix-pgsql ca-certificates tini \
         python3-minimal python3-psycopg2 \
    && rm -rf /var/lib/apt/lists/*

# Postfix insists on a writable spool it owns; the runtime volume supplies it.
# The shared SASL group — see images/dovecot/Dockerfile. Fixed gid on both
# sides so Dovecot can create the auth socket group-readable by Postfix
# without either image depending on the other's packaging numbers.
RUN set -eux; \
    groupadd --system --gid 2102 smtpauth; \
    usermod -aG smtpauth postfix

# NE4's control plane. It runs inside this container because this is the only
# place that already owns the Postfix spool - the alternatives were handing the
# Native API a Docker socket, giving it a writable spool mount, or letting
# Django shell out to `docker exec`, and all three are worse.
#
# python3 and psycopg2 come from Debian rather than pip: no compiler, no wheel
# build, and they are patched by the same apt stream as Postfix itself.
COPY engine_control.py /usr/local/lib/matemail/engine_control.py

VOLUME ["/var/spool/postfix"]

COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh

EXPOSE 25 587
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
