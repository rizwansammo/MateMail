#!/usr/bin/env python3
"""
MateMail Postfix policy bridge.

Postfix cannot call an HTTP API. It speaks the `check_policy_service` protocol:
a stream of `key=value` lines terminated by a blank line, answered with a single
`action=...` line and a blank line. This daemon is the translation layer between
that protocol and MateMail's `/api/internal/smtp/` endpoints, and it contains no
policy of its own — every decision below comes from MateMail.

Runs as a sidecar container in the Mail Engine's Compose project, on the
Mail Engine's network so Postfix can reach it and on `matemail_engine_link` so it
can reach the MateMail backend. It publishes no host port. See
`deploy/engine/README.md`.

## Why DUNNO and never OK

A policy service that answers `OK` tells Postfix to **permit the message and skip
the remaining restrictions in that list**. In `smtpd_recipient_restrictions` the
restriction that follows ours is `reject_unauth_destination` — the check that
stops MateMail from relaying mail for domains it does not host.

So answering `OK` here would turn MateMail into an open relay for anyone whose
request this service happened to approve. `DUNNO` means "I have no objection,
carry on evaluating", which leaves every later restriction in force.

**This daemon must never emit `action=OK`.** There is a test asserting it.

## The two stages, and which one counts

Postfix consults this service twice for one authenticated submission.

| Postfix stage | What we ask MateMail | Counts against the rate limit? |
|---|---|---|
| `RCPT` | authorize (`stage=rcpt`) | no |
| `END-OF-MESSAGE` | authorize and record (`stage=end_of_data`) | yes, once |

`RCPT` fires once per recipient, so a message to five people would otherwise
charge the sender five messages for sending one. Its purpose is to refuse a bad
submission before the body is transferred. `END-OF-MESSAGE` fires exactly once
per message, which is the only stage where a per-message counter is correct.

Note the spelling: Postfix sends `END-OF-MESSAGE` with hyphens. An earlier
version of this file compared against `END_OF_MESSAGE` with underscores, so the
end-of-data stage silently fell through to "some other state, pass" and the rate
limit was never consulted at the only stage that could count it correctly.

## Inbound vs outbound

`sasl_username` is the discriminator, and it is set by Postfix from the SASL
layer — not by the client.

- non-empty → an authenticated submission → outbound policy
- empty at `RCPT` → an external MTA delivering to us → inbound policy
- empty at `END-OF-MESSAGE` → nothing left to decide → `DUNNO`

That last line is load-bearing. The Mail Engine injects its own mail — watchdog
probes, quarantine digests — unauthenticated over the loopback and internal
network. Those messages are not MateMail customer mail, MateMail knows nothing
about their recipients, and asking the inbound policy about them would answer
"domain not hosted here" and break the engine's own monitoring.

## Failure behaviour

If MateMail cannot be reached, the answer is `DEFER_IF_PERMIT`, never `DUNNO`.

`DEFER_IF_PERMIT` is chosen over a plain `DEFER` deliberately: it defers only
where the rest of the restriction chain would otherwise have permitted, so a
relay attempt that a later restriction would reject outright is still rejected
rather than being softened to a retryable 4xx during an outage. An outage must
not become an open relay, and it must not become a permanent bounce either.
"""
import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request

# ── Configuration ────────────────────────────────────────────────────────────
#
# The backend is addressed by its Compose service name on `matemail_engine_link`,
# which is an internal network. There is no host port and no published socket in
# this path — see DEC-014 for why the reverse direction is built the same way.
DJANGO_INTERNAL_URL = os.environ.get(
    "DJANGO_INTERNAL_URL", "http://backend:8000"
).rstrip("/")
INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET", "")

#: Bound inside the container. The Compose service gives it a fixed address on
#: the engine network; nothing is published to the host.
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "10031"))

#: Shorter than Postfix's own policy timeout (100s by default) so that a slow
#: MateMail produces our deliberate DEFER rather than Postfix's generic one.
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "8"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

INBOUND_PATH = "/api/internal/smtp/inbound/"
OUTBOUND_PATH = "/api/internal/smtp/outbound/"

#: Postfix's spelling. Hyphens, upper case.
STATE_RCPT = "RCPT"
STATE_END_OF_MESSAGE = "END-OF-MESSAGE"

#: What we answer when we have no objection. Never "OK" — see the module
#: docstring; "OK" would skip `reject_unauth_destination`.
PASS = "DUNNO"

#: What we answer when MateMail cannot be consulted.
FAIL_CLOSED = "DEFER_IF_PERMIT Mail policy is temporarily unavailable, please retry"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("matemail.policy")

if not INTERNAL_API_SECRET:
    logger.error(
        "INTERNAL_API_SECRET is not set. MateMail will refuse every policy call "
        "and this bridge will defer all mail. Set it in the engine environment."
    )


# ── MateMail HTTP client ─────────────────────────────────────────────────────


def call_matemail(path: str, payload: dict) -> dict | None:
    """POST to MateMail. Returns the parsed body, or None if it could not be asked."""
    request = urllib.request.Request(
        f"{DJANGO_INTERNAL_URL}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Internal-Secret": INTERNAL_API_SECRET,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # A 403 here means the shared secret is wrong. That is a configuration
        # fault, and it must fail closed rather than pass mail unchecked.
        detail = ""
        try:
            detail = exc.read().decode(errors="replace")[:200]
        except Exception:
            pass
        logger.error("MateMail %s returned HTTP %s: %s", path, exc.code, detail)
        return None
    except Exception as exc:
        logger.error("MateMail %s could not be reached: %s", path, exc)
        return None


def to_postfix_action(response: dict | None) -> str:
    """Translate a MateMail decision into a Postfix action."""
    if response is None:
        return FAIL_CLOSED

    action = str(response.get("action", "")).upper()
    reason = " ".join(str(response.get("reason", "")).split())

    if action == "OK":
        return PASS
    if action == "REJECT":
        return f"REJECT {reason}" if reason else "REJECT"
    if action == "DEFER":
        return f"DEFER_IF_PERMIT {reason}" if reason else "DEFER_IF_PERMIT"

    # An answer we do not understand is not permission. MateMail is the
    # authority here, and an unrecognised reply means we failed to ask it
    # properly — which is the same situation as not reaching it at all.
    logger.error("Unrecognised MateMail action %r — failing closed", action)
    return FAIL_CLOSED


# ── Policy decision ──────────────────────────────────────────────────────────


def decide(attrs: dict) -> str:
    """
    Map one Postfix policy request to an action.

    Pure apart from the HTTP call, so the routing rules are testable without a
    Postfix or a Django running.
    """
    state = attrs.get("protocol_state", "").upper()
    sasl_username = attrs.get("sasl_username", "").strip()
    sender = attrs.get("sender", "").strip()
    recipient = attrs.get("recipient", "").strip()
    client_address = attrs.get("client_address", "").strip()

    if sasl_username:
        # ── Authenticated submission ────────────────────────────────────────
        if state == STATE_RCPT:
            stage = "rcpt"
        elif state == STATE_END_OF_MESSAGE:
            stage = "end_of_data"
        else:
            # MAIL, CONNECT, EHLO and friends. The decision needs a recipient or
            # a completed message; there is nothing to ask yet.
            return PASS

        response = call_matemail(
            OUTBOUND_PATH,
            {
                "sender": sender,
                "sasl_username": sasl_username,
                "stage": stage,
                "client_address": client_address,
            },
        )
        action = to_postfix_action(response)
        logger.info(
            "OUTBOUND[%s] sasl=%s sender=%s -> %s",
            stage, sasl_username, sender, action,
        )
        return action

    # ── Unauthenticated ─────────────────────────────────────────────────────
    if state == STATE_END_OF_MESSAGE:
        # Decided already at RCPT. Reaching MateMail again here would also drag
        # the engine's own internal injections into customer policy.
        return PASS

    if state and state != STATE_RCPT:
        return PASS

    if not recipient:
        return PASS

    response = call_matemail(
        INBOUND_PATH,
        {
            "recipient": recipient,
            "sender": sender,
            "client_address": client_address,
        },
    )
    action = to_postfix_action(response)
    logger.info(
        "INBOUND recipient=%s sender=%s client=%s -> %s",
        recipient, sender, client_address, action,
    )
    return action


# ── Postfix policy protocol ──────────────────────────────────────────────────


async def read_request(reader: asyncio.StreamReader) -> dict | None:
    """
    Read one policy request. Returns None when the peer has closed.

    Postfix keeps a connection open across several requests, so this is called
    in a loop rather than once per connection.
    """
    attrs: dict[str, str] = {}
    while True:
        raw = await reader.readline()
        if not raw:
            return None
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            return attrs
        key, sep, value = line.partition("=")
        if sep:
            attrs[key.strip().lower()] = value.strip()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer = writer.get_extra_info("peername", ("?", 0))
    loop = asyncio.get_running_loop()
    try:
        while True:
            attrs = await read_request(reader)
            if attrs is None:
                return
            if not attrs:
                continue

            # The HTTP call is blocking; keep it off the event loop so one slow
            # request cannot stall every other Postfix connection.
            action = await loop.run_in_executor(None, decide, attrs)

            writer.write(f"action={action}\n\n".encode())
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    except Exception:
        logger.exception("Error handling policy connection from %s:%s", *peer)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(
        handle_client, LISTEN_HOST, LISTEN_PORT, reuse_address=True
    )
    host, port = server.sockets[0].getsockname()[:2]
    logger.info("MateMail policy bridge listening on %s:%s", host, port)
    logger.info("Asking MateMail at %s", DJANGO_INTERNAL_URL)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down.")
