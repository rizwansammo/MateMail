# MateMail Native Engine — Olefy image.
#
# WHAT THIS CONFIGURES
#   Olefy: a small TCP service that runs oletools against OLE/macro content in
#   Office attachments, on behalf of Rspamd.
#
# WHY IT IS SHAPED THIS WAY
#   Macro-bearing Office documents are the dominant malware vector in business
#   email — exactly MateMail's market — so NE0.17 retains this scanner rather
#   than dropping it to save a container. No upstream image exists, so it is
#   built here under repository control.
#
#   Stateless by design: it scans what it is given and keeps nothing.
#
# WHICH PHASE OWNS IT
#   NE1 (service foundation). NE3 wires Rspamd's external_services to it.

# The previous floating image reference silently changed every build and
# carried a stale libuuid package in the production image. Review/pin each
# updated official Python digest and apply supported Alpine patch updates.
FROM python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a

RUN apk upgrade --no-cache \
    && apk add --no-cache tini \
    && python -m pip install --no-cache-dir oletools==0.60.2 \
    && python -m pip check

WORKDIR /app
COPY olefy.py /app/olefy.py

USER nobody
EXPOSE 10055
ENTRYPOINT ["/sbin/tini", "--", "python3", "-u", "/app/olefy.py"]
