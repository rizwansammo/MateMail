#!/usr/bin/env python3
"""
MateMail Native Engine — Postfix control plane.

WHY THIS EXISTS AND WHY IT LIVES HERE
    NE4 has to expose queue and quarantine operations, and enforce a per-mailbox
    submission rate limit. Both need to touch Postfix itself.

    The alternatives were all worse:

      Docker socket in the Native API   hands the API root on the host
      writable spool mount in the API   two writers on a queue directory, and a
                                        path-traversal bug becomes spool writes
      `docker exec` from Django         couples MateMail to container names

    So the smallest privileged surface wins: a daemon inside the Postfix
    container, which is the only place that already legitimately owns the spool.
    It speaks two narrow protocols and nothing else, and it adds no service to
    the Compose topology — it runs beside Postfix in the container that already
    exists.

WHAT IT SPEAKS
    1. An HTTP control API on the engine network, shared-secret authenticated,
       used only by the Native API. Queue and quarantine.
    2. Postfix's policy delegation protocol on loopback, used only by Postfix's
       own smtpd. Rate limiting.

WHAT IT REFUSES TO DO
    It runs no shell. Every Postfix invocation is a fixed argv list, and the
    only caller-supplied element is a queue id that has already been matched
    against a strict pattern. There is no code path that concatenates input into
    a command, a path, or a SQL string.
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg2

LOG_PREFIX = "engine-control"


def log(message):
    sys.stderr.write(f"{LOG_PREFIX}: {message}\n")
    sys.stderr.flush()


# ── Queue identifiers ───────────────────────────────────────────────────────
#
# Postfix uses two queue-id formats: the classic hexadecimal one, and the long
# form enabled by `enable_long_queue_ids` which is base-52-ish. Both are
# alphanumeric and bounded, so one strict pattern covers them.
#
# This is the ONLY validation that matters for command safety: a queue id is the
# single piece of caller-controlled data that reaches `postsuper`, and it
# reaches it as one element of a fixed argv list, never through a shell.
_QUEUE_ID = re.compile(r"^[A-Za-z0-9]{6,32}$")


def valid_queue_id(value):
    return isinstance(value, str) and bool(_QUEUE_ID.match(value))


CONTROL_SECRET = os.environ.get("NATIVE_CONTROL_SECRET", "")
CONTROL_PORT = int(os.environ.get("NATIVE_CONTROL_PORT", "8460"))
POLICY_PORT = int(os.environ.get("NATIVE_POLICY_RATE_PORT", "10032"))

REDIS_HOST = os.environ.get("NATIVE_REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("NATIVE_REDIS_PORT", "6379"))

DB_DSN = (
    f"host={os.environ.get('NATIVE_DB_HOST', 'db')} "
    f"port={os.environ.get('NATIVE_DB_PORT', '5432')} "
    f"dbname={os.environ.get('NATIVE_DB_NAME', 'matemail_engine')} "
    f"user={os.environ.get('NATIVE_POSTFIX_DB_USER', 'engine_ro_postfix')} "
    f"password={os.environ.get('NATIVE_POSTFIX_DB_PASSWORD', '')} "
    f"connect_timeout=5"
)


# ── Postfix queue ───────────────────────────────────────────────────────────


def _run(argv, timeout=20):
    """Run a fixed argv list. No shell, ever."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, shell=False
    )


def read_queue():
    """
    The real Postfix queue, as structured data.

    `postqueue -j` emits one JSON object per line. Postfix has supported it
    since 3.1 and this image is 3.7, so nothing here parses the human-readable
    `mailq` table — that format is for operators and changes between versions.
    """
    result = _run(["postqueue", "-j"])
    if result.returncode != 0:
        raise RuntimeError(f"postqueue failed: {result.stderr.strip()[:200]}")

    items = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue

        recipients, reason = [], None
        for entry in raw.get("recipients") or []:
            if entry.get("address"):
                recipients.append(entry["address"])
            # Postfix records the delay reason per recipient; the first one is
            # representative and is what an operator needs to see.
            if reason is None and entry.get("delay_reason"):
                reason = entry["delay_reason"]

        items.append({
            "queue_id": raw.get("queue_id"),
            "queue_name": raw.get("queue_name"),
            "sender": raw.get("sender"),
            "recipients": recipients,
            "message_size": raw.get("message_size"),
            "arrival_time": raw.get("arrival_time"),
            "age_seconds": (
                int(time.time()) - int(raw["arrival_time"])
                if raw.get("arrival_time") else None
            ),
            "reason": reason,
        })
    return items


# A held message is quarantined; everything else is ordinary queue traffic. The
# distinction is Postfix's own `queue_name`, not a guess: `hold` is only ever
# reached deliberately, by the header_checks rule the quarantine design installs
# or by `postsuper -h`.
HOLD_QUEUE = "hold"


def queue_status():
    return [i for i in read_queue() if i.get("queue_name") != HOLD_QUEUE]


def quarantine_items():
    return [i for i in read_queue() if i.get("queue_name") == HOLD_QUEUE]


def _queue_ids():
    return {i["queue_id"] for i in read_queue() if i.get("queue_id")}


def cancel_message(queue_id):
    """
    Delete exactly one queued message.

    Idempotent by contract: deleting something already gone is a success, not an
    error, because the caller's intent — "this must not be delivered" — is
    satisfied either way. A cancel that failed noisily on a message Postfix had
    just delivered or expired would push operators toward retrying blindly.
    """
    if not valid_queue_id(queue_id):
        raise ValueError("queue id is not well formed")
    result = _run(["postsuper", "-d", queue_id])
    # postsuper reports what it did on stderr, including "no such queue file".
    combined = (result.stdout + result.stderr).lower()
    if result.returncode != 0 and "no such" not in combined:
        raise RuntimeError(f"postsuper failed: {result.stderr.strip()[:200]}")
    return {"queue_id": queue_id, "removed": "no such" not in combined}


def release_message(queue_id):
    """
    Release exactly one HELD message back into the delivery path.

    `postsuper -H` un-holds; it cannot change a sender, a recipient or the
    content. That is the reason quarantine is modelled on the hold queue rather
    than on a re-injection step: release is structurally incapable of altering
    who the message was for, so there is no recipient-substitution surface here.
    """
    if not valid_queue_id(queue_id):
        raise ValueError("queue id is not well formed")

    # Only a HELD message may be released. Without this check a caller could
    # pass any queue id and this would silently succeed against ordinary mail.
    held = {i["queue_id"] for i in quarantine_items()}
    if queue_id not in held:
        # Already released, or never held. Idempotent for the same reason cancel
        # is: the requested end state is "no longer quarantined".
        return {"queue_id": queue_id, "released": False, "reason": "not in quarantine"}

    result = _run(["postsuper", "-H", queue_id])
    combined = (result.stdout + result.stderr).lower()
    if result.returncode != 0 and "no such" not in combined:
        raise RuntimeError(f"postsuper failed: {result.stderr.strip()[:200]}")

    # Un-holding moves the message to the DEFERRED queue, where Postfix would
    # wait out its normal backoff — up to `queue_run_delay`, 300s by default —
    # before trying again. An operator who has just released a message expects
    # it to move, not to sit for five more minutes, so delivery is attempted
    # now. The flush is engine-wide because Postfix has no per-message trigger;
    # that is harmless, since it only asks Postfix to retry what it would have
    # retried anyway.
    flush = _run(["postqueue", "-f"])
    if flush.returncode != 0:
        log(f"released {queue_id} but the queue flush failed: "
            f"{flush.stderr.strip()[:120]}")
    return {"queue_id": queue_id, "released": True}


# ── Rate limiting ───────────────────────────────────────────────────────────


def _redis_command(*args):
    """
    Minimal RESP client.

    Two commands are needed — INCR and EXPIRE — so a dependency for them would
    be more surface than code. Failures raise; the caller decides the policy.
    """
    payload = bytearray(b"*%d\r\n" % len(args))
    for arg in args:
        encoded = str(arg).encode()
        payload += b"$%d\r\n%s\r\n" % (len(encoded), encoded)

    with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=3) as sock:
        sock.sendall(bytes(payload))
        data = sock.recv(4096)
    if not data:
        raise RuntimeError("redis returned nothing")
    if data[:1] == b"-":
        raise RuntimeError(data.decode(errors="replace").strip())
    if data[:1] == b":":
        return int(data[1:data.index(b"\r\n")])
    return data


def lookup_limit(address):
    """The configured limit for one mailbox, or None if nothing is configured."""
    with psycopg2.connect(DB_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT messages, window_seconds FROM postfix_rate_limit "
                "WHERE address = %s",
                (address,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {"messages": int(row[0]), "window_seconds": int(row[1])}


def rate_verdict(sasl_username):
    """
    Decide one submission. Returns a Postfix policy action string.

    THE IDENTITY IS THE AUTHENTICATED LOGIN, never the envelope sender. A
    mailbox that is entitled to send as an alias still spends its OWN budget,
    and an alias or forwarding destination cannot become a separate identity to
    spread traffic across — which is exactly what limiting by envelope sender
    would allow.
    """
    if not sasl_username:
        # Not authenticated submission. Relay control already rejected anything
        # that had no business here; this service has no opinion on the rest.
        return "action=DUNNO"

    address = sasl_username.strip().lower()
    try:
        limit = lookup_limit(address)
    except Exception as exc:                     # noqa: BLE001
        # The LOOKUP failing is different from the COUNTER failing, and is left
        # permissive on purpose.
        #
        # This path means the engine database is unreachable — and in that state
        # Postfix's own `virtual_mailbox_maps` and `smtpd_sender_login_maps`
        # lookups are failing too, so submission is already being deferred by
        # the restrictions that run BEFORE this service. Deferring again here
        # would change nothing except which component gets blamed.
        #
        # It also cannot be made strict safely: with no database there is no way
        # to know which mailboxes have a limit, so failing closed would block
        # every mailbox, including the ones nobody ever metered.
        log(f"rate lookup failed ({type(exc).__name__}); leaving the decision to "
            f"the restrictions that already ran")
        return "action=DUNNO"

    if not limit or limit["messages"] == 0:
        # No row means nothing configured; 0 means an explicit "no limit", which
        # is how the RateLimit DTO spells it.
        return "action=DUNNO"

    window = limit["window_seconds"]
    bucket = int(time.time()) // window
    key = f"ne4:rl:{address}:{window}:{bucket}"
    try:
        count = _redis_command("INCR", key)
        if count == 1:
            # Only the first writer sets the TTL, so a burst cannot keep
            # extending the window and turn a fixed window into a sliding one.
            _redis_command("EXPIRE", key, window)
    except Exception as exc:                     # noqa: BLE001
        # FAIL CLOSED. This mailbox HAS a limit, and without the counter there
        # is no way to tell whether it has been reached. Allowing the message
        # would accept it with no enforcement at all — which is indistinguishable
        # from having no limit, for exactly the mailboxes somebody deliberately
        # capped.
        #
        # Deferring is temporary and costs nothing permanent: the sender retries,
        # nothing is lost, and an operator sees mail queuing rather than a limit
        # quietly not applying. A mailbox with NO configured limit never reaches
        # this line — it returned DUNNO above, before Redis was touched — so an
        # outage does not block traffic that was never being metered.
        log(f"redis unavailable ({type(exc).__name__}); deferring a submission "
            f"from a rate-limited mailbox")
        return ("action=DEFER_IF_PERMIT 4.7.1 Submission rate limiting is "
                "temporarily unavailable; try again later")

    if count > limit["messages"]:
        # 4xx, NOT 5xx. A rate limit is a statement about timing, not about the
        # message: the sender should try later, and a well-behaved client will.
        # A permanent rejection would destroy mail that was never wrong, and
        # would generate a bounce for what is a throttling decision.
        return ("action=DEFER_IF_PERMIT 4.7.1 Submission rate limit exceeded "
                "for this mailbox; try again later")
    return "action=DUNNO"


class PolicyServer(threading.Thread):
    """
    Postfix policy delegation.

    Bound to loopback INSIDE the container: the only client is the smtpd running
    beside it, so this protocol is never on the engine network at all.
    """

    daemon = True

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", POLICY_PORT))
        server.listen(64)
        log(f"rate-limit policy service on 127.0.0.1:{POLICY_PORT}")
        while True:
            try:
                conn, _ = server.accept()
                threading.Thread(target=self._serve, args=(conn,), daemon=True).start()
            except Exception as exc:             # noqa: BLE001
                log(f"policy accept failed: {type(exc).__name__}")

    def _serve(self, conn):
        with conn:
            conn.settimeout(30)
            buffer = b""
            attributes = {}
            while True:
                try:
                    chunk = conn.recv(4096)
                except Exception:                # noqa: BLE001
                    return
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if line:
                        text = line.decode(errors="replace")
                        if "=" in text:
                            key, value = text.split("=", 1)
                            attributes[key.strip()] = value.strip()
                        continue
                    # Blank line: the request is complete.
                    verdict = rate_verdict(attributes.get("sasl_username", ""))
                    try:
                        conn.sendall((verdict + "\n\n").encode())
                    except Exception:            # noqa: BLE001
                        return
                    attributes = {}


# ── HTTP control API ────────────────────────────────────────────────────────


class ControlHandler(BaseHTTPRequestHandler):
    server_version = "MateMailEngineControl"
    sys_version = ""

    def log_message(self, fmt, *args):           # quieter, and no client data
        log("control %s" % (fmt % args))

    def _authorised(self):
        if not CONTROL_SECRET:
            return False
        import hmac
        provided = self.headers.get("X-Engine-Control-Secret", "")
        return hmac.compare_digest(provided.encode(), CONTROL_SECRET.encode())

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
            return
        if not self._authorised():
            self._send(403, {"error": "unauthorized"})
            return
        try:
            if self.path == "/queue":
                self._send(200, {"items": queue_status()})
            elif self.path == "/quarantine":
                self._send(200, {"items": quarantine_items()})
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:                 # noqa: BLE001
            log(f"GET {self.path} failed: {type(exc).__name__}: {exc}")
            self._send(500, {"error": type(exc).__name__})

    def do_POST(self):
        if not self._authorised():
            self._send(403, {"error": "unauthorized"})
            return
        body = self._body()
        queue_id = body.get("queue_id")
        if not valid_queue_id(queue_id):
            self._send(400, {"error": "queue_id is not well formed"})
            return
        try:
            if self.path == "/queue/cancel":
                self._send(200, cancel_message(queue_id))
            elif self.path == "/quarantine/release":
                self._send(200, release_message(queue_id))
            else:
                self._send(404, {"error": "not found"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:                 # noqa: BLE001
            log(f"POST {self.path} failed: {type(exc).__name__}: {exc}")
            self._send(500, {"error": type(exc).__name__})


def main():
    if not CONTROL_SECRET:
        log("NATIVE_CONTROL_SECRET is unset — refusing to start the control API")
        sys.exit(78)

    PolicyServer().start()
    server = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), ControlHandler)
    server.daemon_threads = True
    log(f"control API on 0.0.0.0:{CONTROL_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
