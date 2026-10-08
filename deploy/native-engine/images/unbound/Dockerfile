# MateMail Native Engine — Unbound security candidate for Phase 2.
#
# Upstream Unbound 1.26.1, not the older Alpine 3.24 stable 1.25.x package.
# Fetch from NLnet Labs over TLS and verify the exact source SHA256 BEFORE
# unpacking. Multi-stage keeps build tools out of the runtime image.
# Never build on MateServer: isolated PR CI verifies this candidate first.
#
# Runtime invariants carried from the existing 1.22.0 deployment:
# unbound uid:gid 100:101, /var/lib/unbound trust-anchor volume,
# UDP/TCP 53, no root process, DNSSEC validation (config is bind mounted).
FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS builder
RUN apk add --no-cache build-base ca-certificates curl expat-dev libevent-dev openssl-dev
WORKDIR /usr/src
ARG UNBOUND_VERSION=1.26.1
ARG UNBOUND_SHA256=35a6dc0e425a9282c3426d9a3043144011bf0534aed4b73ab62c52aee0af1503
RUN set -eux; \
    curl --fail --location --silent --show-error --retry 3 \
      "https://nlnetlabs.nl/downloads/unbound/unbound-${UNBOUND_VERSION}.tar.gz" -o unbound.tar.gz; \
    echo "${UNBOUND_SHA256}  unbound.tar.gz" | sha256sum -c -; \
    tar -xzf unbound.tar.gz; \
    cd "unbound-${UNBOUND_VERSION}"; \
    ./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var \
        --with-ssl --with-libevent --with-libexpat=/usr --with-pthreads \
        --with-chroot-dir= --with-run-dir=; \
    make -j2; \
    make DESTDIR=/stage install

FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
RUN apk add --no-cache ca-certificates openssl libevent expat bind-tools drill \
    && addgroup -S -g 101 unbound \
    && adduser -S -D -H -u 100 -G unbound -s /sbin/nologin unbound \
    && mkdir -p /var/lib/unbound \
    && chown 100:101 /var/lib/unbound
COPY --from=builder /stage/usr/sbin/ /usr/sbin/
COPY --from=builder /stage/usr/lib/ /usr/lib/
RUN set -eux; \
    unbound -V | grep 'Version 1.26.1'; \
    unbound-anchor -a /var/lib/unbound/root.key || true; \
    test -s /var/lib/unbound/root.key; \
    chown -R 100:101 /var/lib/unbound
USER unbound
EXPOSE 53/udp 53/tcp
ENTRYPOINT ["unbound", "-d", "-c", "/etc/unbound/unbound.conf"]
