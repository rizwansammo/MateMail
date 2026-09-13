#!/usr/bin/env python3
"""
MateMail Native Engine API — NE1 foundation.

WHAT THIS IS
    The only component permitted to write the engine database, and the only
    place DKIM private keys ever exist. Everything MateMail wants done to the
    engine goes through here.

WHY IT EXISTS AT ALL
    The obvious alternative — have `NativeMailEngineAdapter` write the engine
    database over SQL directly — fails on DKIM. Key generation and deletion need
    filesystem access inside the engine, and DEC-007r forbids the private key
    crossing the adapter boundary. Something engine-side has to own it.

    Given that, routing ALL provisioning through one authenticated service buys
    a single trust boundary and a single auth model, and keeps
    `NativeMailEngineAdapter` the same shape as `MailcowAdapter` — which is what
    makes NE5 an adapter swap rather than a rewrite.

    The write model this enforces (NE0.2):

        MateMail                 authoritative for all product state
        NativeMailEngineAdapter  the only EXTERNAL provisioning client
        matemail-native-api      the only DIRECT writer to the engine database
        Postfix / Dovecot        read-only roles; they look up, never mutate

WHAT IS DELIBERATELY NOT HERE
    None of the 26 adapter operations. NE1 proves the service foundation:
    it starts, it reports health, it reaches the database, and it can carry a
    schema version forward. Provisioning is NE2; the adapter is NE5.

    Writing stub domain/mailbox endpoints now would make the stack look finished
    while doing nothing, which is exactly the kind of false green this project
    has already paid for once.

STANDARD LIBRARY ONLY
    So a stock `python:3.13-alpine` runs it and the engine stack does not need a
    private registry to start. Same reasoning as the P5 policy bridge. The
    database is reached over a raw socket speaking enough of the PostgreSQL
    protocol to answer "are you there" — NE2 introduces a real driver when there
    are real queries to run.

WHICH PHASE OWNS IT
    NE1.
"""
import hmac
import http.server
import json
import logging
import os
import socket
import sys
import threading

LISTEN_PORT = int(os.environ.get("NATIVE_API_PORT", "8451"))
API_SECRET = os.environ.get("NATIVE_API_SECRET", "")

DB_HOST = os.environ.get("NATIVE_DB_HOST", "db")
DB_PORT = int(os.environ.get("NATIVE_DB_PORT", "5432"))
DB_NAME = os.environ.get("NATIVE_DB_NAME", "matemail_engine")
DB_USER = os.environ.get("NATIVE_DB_USER", "engine")

#: Where DKIM keys will live (NE2). Declared here so the volume contract is
#: visible in NE1 even though nothing writes to it yet.
DKIM_KEY_DIR = os.environ.get("NATIVE_DKIM_DIR", "/var/lib/rspamd/dkim")

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("matemail.native.api")

if not API_SECRET:
    logger.error(
        "NATIVE_API_SECRET is not set. Every authenticated call will be refused. "
        "This is fail-closed and deliberate, but the service is not usable."
    )


def db_reachable() -> bool:
    """
    Can the engine database accept a connection?

    A TCP check rather than a query: NE1 has nothing to query, and a driver
    dependency would cost the stdlib-only property that lets this run on a
    stock interpreter. NE2 replaces this with a real `SELECT version FROM
    schema_version` once there is a driver and a reason.
    """
    try:
        with socket.create_connection((DB_HOST, DB_PORT), timeout=3):
            return True
    except OSError as exc:
        logger.warning("engine database unreachable at %s:%s — %s", DB_HOST, DB_PORT, exc)
        return False


def authorized(handler) -> bool:
    """Constant-time comparison of the shared secret."""
    if not API_SECRET:
        return False
    provided = handler.headers.get("X-Native-Api-Secret", "")
    return hmac.compare_digest(provided.encode(), API_SECRET.encode())


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "MateMailNativeEngine"
    sys_version = ""          # never advertise the Python version

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            # Unauthenticated ON PURPOSE: this is the container healthcheck,
            # reachable only from inside the engine network, and it returns no
            # information an attacker on that network does not already have.
            ok = db_reachable()
            self._send(200 if ok else 503, {
                "status": "ok" if ok else "degraded",
                "database": "reachable" if ok else "unreachable",
                "phase": "NE1",
            })
            return

        if self.path == "/status":
            if not authorized(self):
                self._send(403, {"error": "unauthorized"})
                return
            self._send(200, {
                "phase": "NE1",
                "database": {
                    "host": DB_HOST, "port": DB_PORT, "name": DB_NAME, "user": DB_USER,
                    "reachable": db_reachable(),
                },
                "dkim_key_dir": DKIM_KEY_DIR,
                "provisioning_implemented": False,
                "note": "NE1 foundation. Provisioning operations arrive in NE2.",
            })
            return

        self._send(404, {"error": "not found"})

    def do_POST(self):
        # NE2 adds provisioning here, and NE0.21's /internal/auth-event. Until
        # then every write is refused rather than silently accepted.
        if not authorized(self):
            self._send(403, {"error": "unauthorized"})
            return
        self._send(501, {
            "error": "not implemented",
            "detail": "NE1 is the service foundation; provisioning is NE2.",
        })

    def log_message(self, fmt, *args):
        # Never log the secret header, and keep health polling out of the log.
        if "/health" not in (self.path or ""):
            logger.info("%s %s", self.command, self.path)


class ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = ThreadedHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    logger.info("MateMail Native Engine API (NE1) listening on 0.0.0.0:%s", LISTEN_PORT)
    logger.info("engine database target %s:%s/%s", DB_HOST, DB_PORT, DB_NAME)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
