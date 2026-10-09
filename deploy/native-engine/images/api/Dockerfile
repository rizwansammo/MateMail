# MateMail Native Engine — Native API image.
#
# WHAT THIS CONFIGURES
#   A Python runtime carrying the three dependencies the provisioning API needs:
#   bcrypt for mailbox password hashing, cryptography for DKIM key generation,
#   and psycopg for the engine database.
#
# WHY THIS IMAGE EXISTS AT ALL
#   NE1 ran the API on stock `python:3.13-alpine` and said so proudly: standard
#   library only, so the engine stack needs no private registry to start. NE2
#   cannot keep that, and the reason was measured rather than assumed.
#
#   Python 3.13 REMOVED the `crypt` module (PEP 594). On the pinned base image
#   `import crypt` raises ModuleNotFoundError, `hashlib` offers no bcrypt, and
#   `bcrypt`, `passlib` and `cryptography` are all absent. So a stock
#   interpreter cannot produce the BLF-CRYPT hash the pinned Dovecot expects.
#
#   The three ways to avoid this image were all rejected:
#     - store plaintext                 -> never
#     - pick a scheme Python can do     -> weakens the credential to dodge a
#                                          dependency
#     - shell into the Dovecot container to run `doveadm pw`
#                                       -> couples provisioning to another
#                                          container's lifetime, needs an exec
#                                          path between them, and puts the
#                                          plaintext in an argv any process
#                                          listing can read
#
#   So: a minimal image under repository control, exactly as NE0 Step 8
#   prescribes for this situation.
#
#   The compatibility itself is measured, not assumed. `doveadm pw -l` on the
#   pinned Dovecot lists BLF-CRYPT, and `doveadm pw -t` accepts a hash produced
#   by this image's bcrypt — succeeding for the right password and failing for a
#   wrong one. That check matters because Dovecot emits `$2y$` while Python's
#   bcrypt emits `$2b$`.
#
#   Built by GitHub Actions and published to GHCR — never built on MateServer.
#
# WHICH PHASE OWNS IT
#   NE2.

# Updated official Python 3.13 image digest, scanned independently before
# use. The earlier digest shipped libuuid with known fixable vulnerabilities.
# Pin the immutable image: :3.13-alpine by itself drifts between CI builds.
FROM python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Apply Alpine security updates in the candidate build. Runtime images are
# tested and published by GitHub Actions only; production remains digest-pinned.
RUN apk upgrade --no-cache

# BUILD CONTEXT IS THE REPOSITORY ROOT, not this directory, because the
# application source copied below lives outside it. The workflow passes
# `file:` so this Dockerfile is still found here.
#
# musl wheels exist for all three dependencies, so there is no compiler in
# the final image and nothing to strip afterwards.
# Patch and pin installer tooling within the native API image as well.
# A vulnerable installer or build backend is still part of the shipped image.
RUN python -m pip install --no-cache-dir --upgrade \
        "pip==26.2.1" "setuptools==84.0.0"

COPY deploy/native-engine/images/api/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
 && python -m pip check \
 && rm -f /tmp/requirements.txt

# ── The uid is load-bearing ─────────────────────────────────────────────────
#
# MEASURED: in rspamd/rspamd:3.11, /var/lib/rspamd is `drwxr-x--- 11333:11333`
# and the container runs as 11333:11333. A DKIM private key must be mode 0600 —
# owner only — and Rspamd must be able to READ it at NE3. Both are satisfiable
# at once only if the writer and the reader are the same uid.
#
# An earlier draft of this image ran as `nobody` (65534). That user cannot write
# the key directory, and cannot even list it: the mode is 0750 with no world
# bits. DKIM generation would have failed the first time it was called, in
# production, rather than here.
#
# So the API runs as 11333. The name is ours; the number is Rspamd's, and the
# number is the part that matters.
RUN addgroup -g 11333 -S nativeapi \
 && adduser  -u 11333 -S -G nativeapi -H -s /sbin/nologin nativeapi \
 && mkdir -p /var/lib/rspamd/dkim \
 && chown -R 11333:11333 /var/lib/rspamd/dkim \
 && chmod 0700 /var/lib/rspamd/dkim

# Creating the directory here with the right ownership is what makes a FRESH
# named volume land correctly: Docker seeds an empty volume from the image
# directory it covers, ownership included. Proven in NE1 with the 5000:5000
# vmail volumes.
VOLUME ["/var/lib/rspamd/dkim"]

# ── The application source is BAKED IN ──────────────────────────────────────
#
# NE1 and NE2A mounted it from the host instead, and the comment here used to
# defend that as keeping the image "about the dependency set". It was wrong,
# and NE2B made the cost concrete: once the API became a digest-pinned image,
# a host bind mount at the same path OVERLAID the very code the digest was
# supposed to guarantee. The digest then attested to bcrypt, cryptography and
# psycopg — and to nothing about the provisioning logic, the DKIM lifecycle or
# the migrations actually executing.
#
# An image whose digest does not cover its own code is not a pinned
# deployment; it is a pinned runtime wrapped around whatever happens to be on
# the disk underneath.
#
# The migrations travel with it for the same reason: the schema the API
# applies must be the schema that build was tested against, not whatever
# version of 002_provisioning.sql the host filesystem happens to carry.
COPY engine/native_api /opt/matemail/native_api

WORKDIR /opt/matemail

USER 11333:11333

EXPOSE 8451
ENTRYPOINT ["python3", "-u", "/opt/matemail/native_api/app.py"]
