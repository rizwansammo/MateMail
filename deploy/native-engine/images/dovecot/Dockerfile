# MateMail Native Engine — Dovecot image.
#
# WHAT THIS CONFIGURES
#   The upstream Dovecot build, unchanged, with the process and storage
#   identities the Native Engine architecture actually requires:
#
#       vmail     5000:5000   owns the mail store
#       dovecot   system      internal service identity
#       dovenull  system      pre-auth/login identity, owns no mail
#
# WHY IT IS SHAPED THIS WAY
#   MEASURED, not assumed. `dovecot/dovecot:2.4.5` ships exactly one mail
#   account, `vmail` at 1000:1000, and no `dovecot` or `dovenull` at all, and it
#   runs the daemon as that unprivileged account. Three consequences follow, all
#   of them observed during NE1 rather than predicted:
#
#     1. Dovecot's default internal and login identities do not exist, so every
#        internal service dies at start — "Group doesn't exist: dovecot", first
#        from `auth`, then `dict`, then the next one.
#     2. An unprivileged daemon cannot fchown /run/dovecot/login, so a separate
#        pre-auth identity is impossible; login and mail collapse onto one user.
#     3. An unprivileged daemon running as uid 1000 cannot reach uid 5000:
#        "setgid(5000 from userdb lookup) failed with euid=1000".
#
#   Collapsing to 1000 would have made all three go away, and would have
#   discarded the reason 5000:5000 was chosen — mailcow compatibility, the NE6
#   platform-sender maildir migration, and unambiguous storage ownership. The
#   architecture is kept; the image is fixed to match it.
#
#   This is a DERIVATIVE, not a rebuild. The upstream Dovecot binaries, their
#   build flags and the base digest are untouched — only /etc/passwd,
#   /etc/group, and the ownership of four directories change. A rebuilt Debian
#   image would have meant owning Dovecot's build and its security updates for
#   no gain, and a community image is not an option for a component holding
#   customer mail. Preserving the upstream build is the point.
#
#   Built by GitHub Actions and published to GHCR — never built on MateServer.
#
# WHICH PHASE OWNS IT
#   NE1. The identity model is a foundation property, not an NE3 decision.

# ── Stage 1: mint correctly-owned directories ────────────────────────────────
# The upstream image has no coreutils — no chown, chmod, mkdir or install — so
# the directories are created here, where those tools exist, and copied in with
# their ownership already correct. This is why a builder stage is needed for
# what looks like a one-line mkdir.
FROM debian:12-slim AS layout

RUN set -eux; \
    mkdir -p /layout/srv/vmail /layout/var/lib/dovecot \
             /layout/var/vmail /layout/var/vmail_index \
             /layout/etc/dovecot; \
    : > /layout/etc/dovecot/postbox-master; \
    chmod 0700 /layout/srv/vmail; \
    chmod 0770 /layout/var/lib/dovecot; \
    chmod 0750 /layout/var/vmail /layout/var/vmail_index; \
    chown -R 5000:5000 /layout/srv/vmail /layout/var/lib/dovecot \
                       /layout/var/vmail /layout/var/vmail_index

# ── Stage 2: the upstream image, with corrected identities ───────────────────
FROM dovecot/dovecot:2.4.5@sha256:43c14def08a57f25107593cbcf64a0a6fbae84a7b9de7aa038513c9b21e91914

USER root

# `groupmod`/`usermod`/`useradd` are present (shadow); coreutils is not. These
# rewrite /etc/passwd and /etc/group through syscalls, so they work here.
#
# vmail's recorded home is /srv/mail, which does not exist in the image, so
# usermod is told not to touch it — the real directory is /srv/vmail and it is
# replaced below with correct ownership.
RUN set -eux; \
    groupmod -g 5000 vmail; \
    usermod  -u 5000 -g 5000 vmail; \
    groupadd --system dovecot; \
    useradd  --system --gid dovecot --no-create-home \
             --home-dir /nonexistent --shell /usr/sbin/nologin dovecot; \
    groupadd --system dovenull; \
    useradd  --system --gid dovenull --no-create-home \
             --home-dir /nonexistent --shell /usr/sbin/nologin dovenull; \
    groupadd --system --gid 2102 smtpauth

# PostBox's master passwd-file is intentionally EMPTY in the image. The runtime
# entrypoint writes the real secret into this path, but Dovecot's auth worker
# runs as the unprivileged `dovecot` identity and therefore cannot read a 0600
# root-owned file. Pre-creating the inode as 0640 root:dovecot gives that one
# worker read access without making the master credential world-readable.
# Shell redirection in the entrypoint truncates this existing file and preserves
# its ownership and mode. No secret is baked into the image.
COPY --from=layout --chown=0:dovecot --chmod=0640 /layout/etc/dovecot/postbox-master /etc/dovecot/postbox-master

# `smtpauth` (gid 2102) is how Postfix reaches Dovecot's SASL socket. NE3 hands
# SMTP authentication to Dovecot over a unix socket on a shared volume rather
# than an inet listener, so a submission password never crosses the container
# network even inside the isolated compose network.
#
# The gid is fixed and identical in the Postfix image. Debian's own `postfix`
# group (102 there) is deliberately NOT reused: that number belongs to the
# packaging, and a silent renumber would leave the socket unreadable and SMTP
# authentication broken for every customer at once.

# Mount points for the native mail store. A named volume inherits the ownership
# of the image directory it covers, so creating these 5000:5000 here is what
# makes the volumes land correctly on first start.
COPY --from=layout --chown=5000:5000 --chmod=0750 /layout/var/vmail       /var/vmail
COPY --from=layout --chown=5000:5000 --chmod=0750 /layout/var/vmail_index /var/vmail_index

# The only two paths in the upstream image still owned by the old uid 1000:
# /srv/vmail (upstream's mail root) and /var/lib/dovecot (upstream's state dir).
# Our configuration uses neither — mail lives in /var/vmail and state_dir is
# /run/dovecot — but a directory owned by a uid that no longer has a name is
# exactly the kind of thing that surfaces three phases later, so it is corrected
# here rather than left behind.
#
# COPY cannot do it: copying a directory onto one that ALREADY EXISTS merges the
# contents and leaves the destination's own ownership untouched. Measured, not
# assumed — /srv/vmail stayed 1000:1000 through a `COPY --chown=5000:5000`.
#
# The bind mount lends this one RUN a `chown` from the layout stage and leaves
# nothing behind in the image: no coreutils, no package manager, no new layer
# contents beyond the ownership bits themselves.
# The snakeoil key is handled in the same pass. Upstream leaves it group-owned
# by vmail so the daemon can read it; we never use it — NE1 runs `ssl = no` and
# NE3 supplies real certificates through the native_tls volume — so it is given
# to root alone. Tighter than upstream, and nothing is left owned by a numeric
# id with no name behind it.
RUN --mount=type=bind,from=layout,source=/,target=/mnt/layout     /mnt/layout/usr/bin/chown -R 5000:5000 /srv/vmail /var/lib/dovecot  && /mnt/layout/usr/bin/chown 0:0 /etc/ssl/private/ssl-cert-snakeoil.key

# Dovecot's normal privilege model: master starts as root, creates its sockets,
# and drops to dovenull for pre-auth work, dovecot for internal services and
# vmail for mail access. This is NOT a privileged container — it needs no added
# capabilities, no host networking and no `privileged: true`; it uses only the
# default container capability set that any root process already has.
USER root

# The engine database password cannot come from dovecot.conf — that file is in
# Git — and Dovecot cannot expand an environment variable into a setting, which
# was measured against this image rather than assumed: `%{env:...}` parses, and
# libpq then reports "fe_sendauth: no password supplied". The entrypoint renders
# the connection into a root-only include before starting Dovecot, and refuses
# to start at all if the password is missing or unrepresentable.
COPY --chown=0:0 --chmod=0755 entrypoint.sh /usr/local/bin/matemail-dovecot-entrypoint

# Upstream's CMD is dropped deliberately: the entrypoint exec's
# `tini -- dovecot -F` itself, so there is one place that decides how Dovecot
# starts rather than two that have to agree.
ENTRYPOINT ["/usr/local/bin/matemail-dovecot-entrypoint"]
CMD []
