#!/usr/bin/env python3
"""
Olefy — OLE/macro scanning service for Rspamd.

WHAT THIS IS
    A small TCP service that accepts an attachment, runs `olevba` over it, and
    returns the analysis. Rspamd's `external_services` module calls it for
    Office document parts and scores the result.

WHY IT EXISTS
    Macro-bearing Office documents are the dominant malware delivery vector in
    business email — exactly the market MateMail serves — which is why NE0.17
    retains this scanner rather than dropping it to save a container. There is
    no upstream image, so the service is small enough to own outright.

WHY IT LOOKS LIKE THIS
    Stateless on purpose: it scans what it is handed and keeps nothing, so there
    is no volume, no persistence and nothing to back up. It binds only inside
    the engine network and is never published.

    Every failure answers rather than hanging. Rspamd is configured with
    `soft_reject_on_timeout`, so a scanner that stops answering DEFERS mail; a
    scanner that answers "I could not parse this" lets Rspamd score it and move
    on. The distinction matters — hanging would turn every malformed attachment
    into a deferred message.

PROTOCOL
    Rspamd's oletools client sends a small header followed by the file bytes,
    and reads a text response. The framing is deliberately forgiving: this
    parses what it needs and ignores the rest, because a protocol nicety is not
    worth deferring mail over.

WHICH PHASE OWNS IT
    NE1 (service foundation). NE3 activates Rspamd's path to it.
"""
import logging
import os
import socketserver
import sys
import tempfile

BIND_ADDRESS = os.environ.get("OLEFY_BINDADDRESS", "0.0.0.0")
BIND_PORT = int(os.environ.get("OLEFY_BINDPORT", "10055"))
LOG_LEVEL = int(os.environ.get("OLEFY_LOGLVL", "20"))

#: Refuse anything larger rather than spending memory on it. Rspamd is
#: configured with the same ceiling, so this is a second line, not the only one.
MAX_BYTES = int(os.environ.get("OLEFY_MAXFILESIZE", str(3 * 1024 * 1024)))

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)-8s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("olefy")


def analyse(payload: bytes) -> str:
    """
    Run olevba over one attachment and return its report.

    Never raises: a scanner that crashes on a malformed document would defer
    every message carrying one, which is a denial of service an attacker can
    trigger with a single broken file.

    NE3 MUST TUNE THE VERDICT, measured during NE1: olevba treats a plain-text
    file as a candidate for VBA source, so non-Office bytes come back as
    "macro: <tmpfile>" rather than "no macros". Harmless while nothing consumes
    the result, but it would be a false positive the moment Rspamd scores it.
    Fixing it needs the real response contract and a sample corpus, which is the
    NE3 integration test — not a guess made here.
    """
    try:
        from oletools.olevba import VBA_Parser
    except ImportError:
        logger.error("oletools is not installed — cannot scan")
        return "[olefy] scanner unavailable"

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
            fh.write(payload)
            tmp = fh.name

        parser = VBA_Parser(tmp)
        if not parser.detect_vba_macros():
            parser.close()
            return "[olefy] no macros"

        lines = []
        for _, _, name, code in parser.extract_macros():
            lines.append(f"[olefy] macro: {name}")
            for kw_type, keyword, description in parser.analyze_macros() or []:
                lines.append(f"[olefy] {kw_type}: {keyword} — {description}")
            break          # one analysis pass is enough for scoring
        parser.close()
        return "\n".join(lines) or "[olefy] macros present"

    except Exception as exc:                      # noqa: BLE001 — see docstring
        logger.warning("scan failed: %s: %s", type(exc).__name__, exc)
        return f"[olefy] unparseable: {type(exc).__name__}"
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


class Handler(socketserver.StreamRequestHandler):
    timeout = 30

    def handle(self):
        try:
            payload = self.rfile.read(MAX_BYTES + 1)
        except Exception as exc:                  # noqa: BLE001
            logger.warning("read failed: %s", exc)
            return

        if not payload:
            return
        if len(payload) > MAX_BYTES:
            logger.info("rejected oversized attachment (%d bytes)", len(payload))
            self.wfile.write(b"[olefy] too large\n")
            return

        result = analyse(payload)
        logger.debug("scanned %d bytes", len(payload))
        self.wfile.write(result.encode() + b"\n")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    logger.info("olefy listening on %s:%s (max %d bytes)",
                BIND_ADDRESS, BIND_PORT, MAX_BYTES)
    with Server((BIND_ADDRESS, BIND_PORT), Handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("shutting down")


if __name__ == "__main__":
    main()
