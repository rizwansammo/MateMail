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

FROM python:3.13-alpine

RUN apk add --no-cache tini \
    && pip install --no-cache-dir oletools==0.60.2

WORKDIR /app
COPY olefy.py /app/olefy.py

USER nobody
EXPOSE 10055
ENTRYPOINT ["/sbin/tini", "--", "python3", "-u", "/app/olefy.py"]
