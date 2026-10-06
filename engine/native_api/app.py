#!/usr/bin/env python3
"""
MateMail Native Engine API — NE2 provisioning.

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
    `NativeMailEngineAdapter` aligned with the MailEngineAdapter contract — which is what
    makes NE5 an adapter swap rather than a rewrite.

    The write model this enforces (NE0.2):

        MateMail                 authoritative for all product state
        NativeMailEngineAdapter  the only EXTERNAL provisioning client
        matemail-native-api      the only DIRECT writer to the engine database
        Postfix / Dovecot        read-only roles; they look up, never mutate

WHY IT IS RPC-SHAPED AND NOT REST
    The thing on the other side is `MailEngineAdapter`, whose operations are
    verbs with idempotency guarantees — `ensure_mailbox`, `rotate_dkim_key`.
    Mapping those onto PUT/PATCH would mean inventing a resource model nobody
    consumes and then translating back, and "ensure" has no HTTP verb. The
    routes name the operations instead, so a reader can line them up against the
    adapter one for one.

WHAT NE2 DELIBERATELY DOES NOT DO
    Nothing here makes Postfix, Dovecot or Rspamd CONSUME this state. The tables
    are authoritative and the keys are on disk, but no lookup is wired and no
    mail is routed by any of it. That is NE3.

WHICH PHASE OWNS IT
    NE2.
"""
import hmac
import http.server
import json
import logging
import os
import socket
import sys
import urllib.parse

import db
import dkim as dkim_lib
import operations
import provisioning
import push
import validation
from validation import ValidationError

LISTEN_PORT = int(os.environ.get("NATIVE_API_PORT", "8451"))
API_SECRET = os.environ.get("NATIVE_API_SECRET", "")

DB_HOST = os.environ.get("NATIVE_DB_HOST", "db")
DB_PORT = int(os.environ.get("NATIVE_DB_PORT", "5432"))
DB_NAME = os.environ.get("NATIVE_DB_NAME", "matemail_engine")
DB_USER = os.environ.get("NATIVE_DB_USER", "engine")

DKIM_KEY_DIR = os.environ.get("NATIVE_DKIM_DIR", "/var/lib/rspamd/dkim")

#: Refuse a body larger than this before reading it. Provisioning payloads are
#: small; anything larger is a mistake or an attempt to exhaust memory.
MAX_BODY_BYTES = 256 * 1024

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

#: Keys that must never appear in a response body. This is a backstop, not the
#: mechanism: the queries in `provisioning` do not select private material in the
#: first place. It exists because a future handler that forgets should fail
#: loudly here rather than ship a private key to MateMail.
FORBIDDEN_RESPONSE_KEYS = frozenset({
    "password", "plaintext", "private_key", "privkey", "private_key_path",
    "secret", "password_hash", "pem",
})


def db_reachable() -> bool:
    """Cheap liveness: can the engine database accept a connection?"""
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


#: Dovecot's own credential, SEPARATE from API_SECRET on purpose.
#:
#: Dovecot needs to tell the engine "this mailbox just logged in", because it
#: cannot write the database itself (migration 003) and the API is the only
#: writer. Handing it API_SECRET to do that would give the most network-exposed
#: component in the engine the ability to create domains, mint mailboxes and
#: rotate DKIM keys. A dedicated secret that opens exactly one endpoint keeps a
#: compromised Dovecot to the damage Dovecot can already do.
POLICY_SECRET = os.environ.get("NATIVE_DOVECOT_POLICY_SECRET", "")


def policy_authorized(handler) -> bool:
    """Constant-time check of Dovecot's auth-policy credential."""
    if not POLICY_SECRET:
        return False
    provided = handler.headers.get("X-Native-Policy-Secret", "")
    return hmac.compare_digest(provided.encode(), POLICY_SECRET.encode())


def _assert_response_is_safe(payload) -> None:
    """Walk a response and refuse to send anything carrying private material."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in FORBIDDEN_RESPONSE_KEYS:
                raise RuntimeError(f"refusing to return field {key!r} to a client")
            _assert_response_is_safe(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_response_is_safe(item)
    elif isinstance(payload, str) and "PRIVATE KEY" in payload:
        raise RuntimeError("refusing to return private key material to a client")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "MateMailNativeEngine"
    sys_version = ""          # never advertise the Python version

    # ── plumbing ────────────────────────────────────────────────────────────

    def _send(self, code: int, payload: dict):
        try:
            _assert_response_is_safe(payload)
        except RuntimeError as exc:
            logger.error("response guard tripped: %s", exc)
            code, payload = 500, {"error": "internal error"}
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        """
        Read and parse the request body.

        The body is NEVER logged. A provisioning payload can contain a mailbox
        password, and a debug line that dumps the request is how plaintext
        credentials end up in a log aggregator forever.
        """
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValidationError("invalid Content-Length")
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValidationError("request body is too large")
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            # Deliberately does not echo the body — it may contain a password.
            raise ValidationError("request body is not valid JSON")
        finally:
            del raw
        if not isinstance(parsed, dict):
            raise ValidationError("request body must be a JSON object")
        return parsed

    def _query(self) -> dict:
        return {
            k: v[0]
            for k, v in urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query
            ).items()
        }

    @property
    def _route(self) -> str:
        return urllib.parse.urlparse(self.path).path.rstrip("/") or "/"

    # ── unauthenticated ─────────────────────────────────────────────────────

    def _handle_health(self):
        """
        Container liveness. Unauthenticated ON PURPOSE: reachable only from
        inside the engine network, and it reveals nothing an attacker already on
        that network does not have.

        Deliberately cheap and non-mutating — no query, no migration, no key
        generation. A healthcheck that changed state would make Docker's restart
        policy a provisioning trigger.
        """
        ok = db_reachable()
        self._send(200 if ok else 503, {
            "status": "ok" if ok else "degraded",
            "database": "reachable" if ok else "unreachable",
            "phase": "NE2",
        })

    # ── authenticated ───────────────────────────────────────────────────────

    def _handle_ready(self):
        """
        Readiness: is this API actually able to serve provisioning?

        Separate from `/health` because the answers differ. A reachable database
        running an older schema is *live* but not *ready*, and serving writes
        against a schema this build was not written for is how half-written rows
        happen. Still non-mutating: it reports the version, it does not migrate.
        """
        try:
            with db.connect() as conn:
                version = db.current_version(conn)
        except Exception as exc:                 # noqa: BLE001 - reported, not raised
            self._send(503, {
                "ready": False,
                "reason": "database unavailable",
                "detail": type(exc).__name__,
            })
            return
        compatible = version >= db.REQUIRED_VERSION
        writable = os.access(DKIM_KEY_DIR, os.W_OK) if os.path.isdir(DKIM_KEY_DIR) else None
        self._send(200 if compatible else 503, {
            "ready": bool(compatible),
            "schema_version": version,
            "required_schema_version": db.REQUIRED_VERSION,
            "dkim_storage_writable": writable,
            "phase": "NE2",
        })

    def _handle_status(self):
        self._send(200, {
            "phase": "NE2",
            "database": {
                "host": DB_HOST, "port": DB_PORT, "name": DB_NAME, "user": DB_USER,
                "reachable": db_reachable(),
            },
            "dkim_key_dir": DKIM_KEY_DIR,
            "provisioning_implemented": True,
            "note": "NE2 provisioning. Mail routing consumes none of this yet (NE3).",
        })

    # ── routing ─────────────────────────────────────────────────────────────

    def do_GET(self):
        route = self._route
        if route == "/health":
            self._handle_health()
            return
        if not authorized(self):
            self._send(403, {"error": "unauthorized"})
            return
        if route == "/ready":
            self._handle_ready()
            return
        if route == "/status":
            self._handle_status()
            return

        handler = _READ_ROUTES.get(route)
        if handler is None:
            self._send(404, {"error": "not found"})
            return
        try:
            with db.connect() as conn:
                result = handler(conn, self._query())
            if result is None:
                self._send(404, {"error": "not found"})
            else:
                self._send(200, result)
        except operations.InvalidIdentifier as exc:
            self._send(400, {"error": str(exc)})
        except ValidationError as exc:
            self._send(400, {"error": exc.message, "field": exc.field})
        except provisioning.NotFound as exc:
            self._send(404, {"error": str(exc)})
        except Exception as exc:                 # noqa: BLE001
            self._fail(exc)

    def _handle_auth_policy_report(self):
        """
        Record a successful login. Dovecot's auth-policy "report" hook.

        WHY THIS EXISTS AT ALL
            `last_login_at` is a column MateMail shows customers and uses to
            find dormant mailboxes. Dovecot knows when a login happened; it must
            not write the database. Dovecot's own `last_login` plugin would need
            write access, so it is not an option. This hook is the seam that
            keeps "one writer" true.

        FAIL-OPEN, DELIBERATELY
            `auth_policy_reject_on_fail = no` on the Dovecot side means a login
            still succeeds when this endpoint is down. Recording a timestamp is
            bookkeeping; refusing a customer their mail because bookkeeping is
            unavailable would turn a cosmetic outage into a real one.

        Only a SUCCESSFUL login is recorded. Dovecot reports failures too, and
        writing those here would let an unauthenticated attacker move a
        timestamp by guessing passwords at the SMTP port.
        """
        body = self._body()
        username = str(body.get("login") or body.get("username") or "").strip().lower()
        # Dovecot sends `success` on the report hook. Anything that is not an
        # explicit success is ignored rather than guessed at.
        if not username or body.get("success") is not True:
            self._send(200, {"recorded": False})
            return
        with db.connect() as conn:
            recorded = provisioning.record_login(conn, username)
        self._send(200, {"recorded": recorded})

    def _handle_push_report(self):
        """
        A committed LMTP delivery, reported by Dovecot's push hook, for PostBox.

        Answered as soon as the report is valid and queued: the relay to
        MateMail runs on its own thread (push.Relay), so Dovecot - and the
        delivery it is finishing - never waits on MateMail. The hook ignores
        the answer anyway; mail was saved before it asked.
        """
        if not push.dovecot_authorized(self.headers.get("X-Native-Push-Secret", "")):
            self._send(403, {"error": "unauthorized"})
            return
        try:
            event = push.mailbox_event(self._body())
        except ValidationError as exc:
            self._send(400, {"error": exc.message, "field": exc.field})
            return
        except Exception as exc:                 # noqa: BLE001
            self._fail(exc)
            return
        self._send(202, {"queued": push.relay.submit(event)})

    def do_POST(self):
        route = self._route
        # PostBox new-mail events carry Dovecot's push credential, and like the
        # policy route below they are checked before - and never with - the
        # provisioning secret.
        if route == "/v1/dovecot/push":
            self._handle_push_report()
            return
        # Checked BEFORE the provisioning secret: this route has its own
        # credential and must not be reachable with it, nor reachable with the
        # provisioning secret's authority.
        # Dovecot POSTs to the configured URL verbatim — it does NOT append
        # "allow"/"report" the way the documentation's examples suggest.
        # Measured against 2.4.1: the request arrived at /v1/dovecot/policy.
        # Both spellings are accepted so a URL with or without the suffix works.
        if route in ("/v1/dovecot/policy", "/v1/dovecot/policy/report"):
            if not policy_authorized(self):
                self._send(403, {"error": "unauthorized"})
                return
            try:
                self._handle_auth_policy_report()
            except Exception as exc:             # noqa: BLE001
                self._fail(exc)
            return
        if not authorized(self):
            self._send(403, {"error": "unauthorized"})
            return
        handler = _WRITE_ROUTES.get(route)
        if handler is None:
            self._send(404, {"error": "not found"})
            return
        try:
            body = self._body()
            with db.connect() as conn:
                result = handler(conn, body)
            self._send(200, result if result is not None else {"ok": True})
        except operations.InvalidIdentifier as exc:
            self._send(400, {"error": str(exc)})
        except ValidationError as exc:
            self._send(400, {"error": exc.message, "field": exc.field})
        except provisioning.NotFound as exc:
            self._send(404, {"error": str(exc)})
        except dkim_lib.DkimError as exc:
            self._send(500, {"error": str(exc)})
        except Exception as exc:                 # noqa: BLE001
            self._fail(exc)

    def _fail(self, exc: Exception):
        """
        Report an unexpected failure without leaking what caused it.

        The exception TYPE is logged, never `str(exc)`: a database driver
        cheerfully includes the failing statement and its parameters in the
        message, and those parameters can include a password hash or, on the
        hashing path, material derived from the plaintext.
        """
        logger.error("unhandled %s on %s %s", type(exc).__name__, self.command, self._route)
        self._send(500, {"error": "internal error"})

    def log_message(self, fmt, *args):
        # Method and path only. Never headers (the secret lives in one) and
        # never the body (a password can live in one).
        if "/health" not in (self.path or ""):
            logger.info("%s %s", self.command, self._route)


# ── write routes ────────────────────────────────────────────────────────────
#
# Each validates its own field set, so an unknown field is refused by the
# endpoint that would have ignored it.

def _domains_ensure(conn, body):
    validation.payload(body, allowed={
        "name", "dkim_selector", "dkim_key_size", "max_mailboxes", "max_aliases",
        "default_quota_mb", "max_quota_mb", "total_quota_mb", "active",
    }, required={"name"})
    return provisioning.ensure_domain(conn, body)


def _domains_set_active(conn, body):
    validation.payload(body, allowed={"name", "active"}, required={"name", "active"})
    provisioning.set_domain_active(conn, body["name"], body["active"])
    return {"ok": True}


def _domains_delete(conn, body):
    validation.payload(body, allowed={"name"}, required={"name"})
    provisioning.delete_domain(conn, body["name"])
    return {"ok": True}


def _mailboxes_ensure(conn, body):
    validation.payload(body, allowed={
        "address", "local_part", "domain", "display_name", "quota_mb", "active",
        "password", "login_enabled", "authorized_senders",
    }, required={"address"})
    password = body.get("password", "") or ""
    return provisioning.ensure_mailbox(conn, body, password)


def _mailboxes_set_active(conn, body):
    validation.payload(body, allowed={"address", "active"}, required={"address", "active"})
    provisioning.set_mailbox_active(conn, body["address"], body["active"])
    return {"ok": True}


def _mailboxes_set_password(conn, body):
    validation.payload(body, allowed={"address", "password"}, required={"address", "password"})
    provisioning.set_mailbox_password(conn, body["address"], body["password"])
    # Deliberately returns nothing about the credential — not the hash, not its
    # length, not a prefix.
    return {"ok": True}


def _mailboxes_set_quota(conn, body):
    validation.payload(body, allowed={"address", "quota_mb"}, required={"address", "quota_mb"})
    provisioning.set_mailbox_quota(conn, body["address"], body["quota_mb"])
    return {"ok": True}


def _mailboxes_delete(conn, body):
    validation.payload(body, allowed={"address"}, required={"address"})
    provisioning.delete_mailbox(conn, body["address"])
    return {"ok": True}


def _aliases_ensure(conn, body):
    validation.payload(body, allowed={"address", "destinations", "active"},
                       required={"address", "destinations"})
    return provisioning.ensure_alias(conn, body)


def _aliases_delete(conn, body):
    validation.payload(body, allowed={"address"}, required={"address"})
    provisioning.delete_alias(conn, body["address"])
    return {"ok": True}


def _forwarding_ensure(conn, body):
    validation.payload(body, allowed={"mailbox_address", "destinations"},
                       required={"mailbox_address"})
    return provisioning.ensure_forwarding(conn, body)


def _forward_groups_ensure(conn, body):
    validation.payload(
        body,
        allowed={
            "address",
            "domain",
            "destinations",
            "sender_policy",
            "allowed_senders",
            "active",
        },
        required={"address", "destinations", "sender_policy"},
    )
    return provisioning.ensure_forward_group(conn, body)


def _forward_groups_delete(conn, body):
    validation.payload(body, allowed={"address"}, required={"address"})
    provisioning.delete_forward_group(conn, body["address"])
    return {"ok": True}


def _dkim_rotate(conn, body):
    validation.payload(body, allowed={"domain", "selector"}, required={"domain"})
    return provisioning.rotate_dkim_key(conn, body["domain"], body.get("selector", "") or "")


def _dkim_reconcile(conn, body):
    """
    Operator-triggered reconciliation.

    Deliberately a POST and deliberately NOT part of /health or /ready: it can
    change database state, and a healthcheck that mutates would make Docker's
    restart policy a provisioning trigger.
    """
    validation.payload(body, allowed=set())
    return {"reconciled": provisioning.reconcile_dkim(conn)}


def _dkim_delete(conn, body):
    validation.payload(body, allowed={"domain"}, required={"domain"})
    provisioning.delete_dkim_key(conn, body["domain"])
    return {"ok": True}


# ── read routes ─────────────────────────────────────────────────────────────
#
# A table rather than a chain of `if route ==`, for the same reason the writes
# are one: the tests dispatch through exactly these functions, so a contract
# test exercises the real handler instead of a second implementation of it that
# can drift.
#
# Returning None means 404.

def _read_domains(conn, query):
    return {"domains": provisioning.list_domains(conn)}


def _read_mailboxes(conn, query):
    return {"mailboxes": provisioning.list_mailboxes(conn, query.get("domain", ""))}


def _read_alias(conn, query):
    return provisioning.get_alias(conn, query.get("address", ""))


def _read_send_as(conn, query):
    address = query.get("address", "")
    return {
        "address": validation.email_address(address),
        "authorized_send_as": provisioning.authorized_send_as(conn, address),
    }


def _read_forwarding(conn, query):
    address = query.get("address", "")
    return {
        "mailbox_address": validation.email_address(address, "mailbox_address"),
        "destinations": provisioning.get_forwarding(conn, address),
    }


def _read_forward_group(conn, query):
    return provisioning.get_forward_group(conn, query.get("address", ""))


def _read_dkim(conn, query):
    return provisioning.get_dkim_public_key(conn, query.get("domain", ""))


# ── NE4: operations ─────────────────────────────────────────────────────────


def _read_usage(conn, query):
    """
    Real consumption, measured by Dovecot.

    An unreachable Dovecot is NOT reported as zero usage. A fabricated number
    here would be believed by whatever renders it, and "your mailbox is empty"
    is a worse answer than "we could not measure it".
    """
    address = validation.email_address(query.get("address", ""))
    try:
        return operations.mailbox_usage(address)
    except operations.OperationUnavailable as exc:
        raise RuntimeError(f"usage unavailable: {exc}") from exc


def _read_rate_limit(conn, query):
    return provisioning.get_mailbox_rate_limit(
        conn, validation.email_address(query.get("address", "")))


def _read_queue(conn, query):
    return {"items": operations.queue_status()}


def _read_quarantine(conn, query):
    return {"items": operations.quarantine_items()}


def _read_retired_storage(conn, query):
    return {"items": provisioning.list_retired_storage(conn, query.get("address", ""))}


def _rate_limit_set(conn, body):
    return provisioning.set_mailbox_rate_limit(
        conn,
        body.get("address", ""),
        body.get("messages"),
        body.get("window", "hour"),
    )


def _rate_limit_clear(conn, body):
    provisioning.clear_mailbox_rate_limit(conn, body.get("address", ""))
    return {"cleared": True}


def _queue_cancel(conn, body):
    return operations.cancel_queue_message(body.get("queue_id", ""))


def _quarantine_release(conn, body):
    return operations.release_quarantine_item(body.get("queue_id", ""))


_READ_ROUTES = {
    "/v1/domains":             _read_domains,
    "/v1/mailboxes":           _read_mailboxes,
    "/v1/aliases":             _read_alias,
    "/v1/mailboxes/send-as":   _read_send_as,
    "/v1/forwarding":          _read_forwarding,
    "/v1/forward-groups":      _read_forward_group,
    "/v1/dkim":                _read_dkim,
    "/v1/mailboxes/usage":     _read_usage,
    "/v1/mailboxes/rate-limit": _read_rate_limit,
    "/v1/queue":               _read_queue,
    "/v1/quarantine":          _read_quarantine,
    "/v1/storage/retired":     _read_retired_storage,
}


_WRITE_ROUTES = {
    "/v1/domains/ensure":          _domains_ensure,
    "/v1/domains/set-active":      _domains_set_active,
    "/v1/domains/delete":          _domains_delete,
    "/v1/mailboxes/ensure":        _mailboxes_ensure,
    "/v1/mailboxes/set-active":    _mailboxes_set_active,
    "/v1/mailboxes/set-password":  _mailboxes_set_password,
    "/v1/mailboxes/set-quota":     _mailboxes_set_quota,
    "/v1/mailboxes/delete":        _mailboxes_delete,
    "/v1/aliases/ensure":          _aliases_ensure,
    "/v1/aliases/delete":          _aliases_delete,
    "/v1/forwarding/ensure":       _forwarding_ensure,
    "/v1/forward-groups/ensure":   _forward_groups_ensure,
    "/v1/forward-groups/delete":   _forward_groups_delete,
    "/v1/dkim/rotate":             _dkim_rotate,
    "/v1/dkim/delete":             _dkim_delete,
    "/v1/dkim/reconcile":          _dkim_reconcile,
    "/v1/mailboxes/rate-limit/set":   _rate_limit_set,
    "/v1/mailboxes/rate-limit/clear": _rate_limit_clear,
    "/v1/queue/cancel":              _queue_cancel,
    "/v1/quarantine/release":        _quarantine_release,
}


class ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    try:
        with db.connect() as conn:
            version = db.apply_migrations(conn)
            logger.info("engine schema at version %d", version)
            # Migration 003 gives the reader roles LOGIN; their passwords come
            # from the environment, because a migration is in Git and a secret
            # must not be. Runs every start so `.env` stays the single source of
            # truth for a credential rotation.
            db.sync_reader_credentials(conn)
            # Repair anything a crash left behind BEFORE serving a request.
            # Idempotent, and it never generates a key — minting one here would
            # publish material no DNS record names.
            provisioning.reconcile_dkim(conn)
    except Exception as exc:                     # noqa: BLE001
        # Start anyway and report unready. Exiting would make the container
        # restart-loop against a database that may simply be slow to come up,
        # and a loop is harder to diagnose than a service that says why it is
        # not ready.
        logger.error("could not apply engine migrations: %s", type(exc).__name__)

    server = ThreadedHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    logger.info("MateMail Native Engine API (NE2) listening on 0.0.0.0:%s", LISTEN_PORT)
    logger.info("engine database target %s:%s/%s", DB_HOST, DB_PORT, DB_NAME)
    # PostBox new-mail events: say once, at start, what is and is not wired,
    # so a half-configured relay is visible rather than silently dropping.
    if push.DOVECOT_PUSH_SECRET and not push.relay.enabled:
        logger.warning(
            "PostBox push: Dovecot may report deliveries but NATIVE_POSTBOX_PUSH_URL "
            "or NATIVE_POSTBOX_PUSH_SECRET is unset - events will not reach MateMail"
        )
    elif push.relay.enabled:
        push.relay.start()
        logger.info("PostBox push relay enabled")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
