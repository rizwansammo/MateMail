# MateMail Native Engine — Unbound image.
#
# WHAT THIS CONFIGURES
#   A minimal validating resolver image built from Alpine's packaged Unbound.
#
# WHY IT IS SHAPED THIS WAY
#   NLnet Labs publishes no official image, and a DNSSEC validator is too
#   security-critical to take from an unvetted community image (NE0.18, Step 8).
#   So we build our own, from a distribution package with a maintained security
#   feed, under repository control.
#
#   Built by GitHub Actions and published to GHCR — never built on MateServer.
#
# WHICH PHASE OWNS IT
#   NE1.

FROM alpine:3.21

RUN apk add --no-cache unbound bind-tools drill \
    && mkdir -p /var/lib/unbound \
    && chown -R unbound:unbound /var/lib/unbound

# Bootstrap the trust anchor at build time; the runtime volume keeps it current.
RUN unbound-anchor -a /var/lib/unbound/root.key || true \
    && chown unbound:unbound /var/lib/unbound/root.key

USER unbound
EXPOSE 53/udp 53/tcp
ENTRYPOINT ["unbound", "-d", "-c", "/etc/unbound/unbound.conf"]
