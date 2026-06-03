#!/usr/bin/env python3
"""
MateMail Postfix policy daemon bridge.

Translates Postfix's check_policy_service TCP protocol into HTTP calls
against the MateMail Django API at /api/internal/smtp/{inbound,outbound}/.

Postfix check_policy_service protocol:
  - Postfix opens a TCP connection and sends key=value lines, one per line.
  - An empty line terminates the request.
  - The daemon responds with "action=VALUE\\n\\n" (trailing empty line required).
  - The connection may be reused for multiple sequential requests.

Action mapping (Postfix → Django → Postfix):
  Django "OK"     → Postfix "DUNNO"             (continue normal processing)
  Django "REJECT" → Postfix "REJECT reason"     (permanent rejection)
  Django "DEFER"  → Postfix "DEFER_IF_PERMIT r" (temporary deferral — client retries)
  Django timeout  → Postfix "DEFER ..."         (fail closed: never accidentally permit)

Inbound vs. outbound detection:
  - sasl_username is non-empty → authenticated submission → outbound policy
  - sasl_username is empty     → delivery from external MTA → inbound policy
  - Only acts on protocol_state=RCPT to avoid duplicate checks per message

Deployment (run on the VPS host, reached by mailcow Postfix via host IP):
  DJANGO_INTERNAL_URL=http://127.0.0.1:8000 \\
  INTERNAL_API_SECRET=your-secret \\
  python3 /opt/matemail/scripts/postfix_policy_bridge.py

  Or via systemd: systemctl start postfix-policy-bridge

Postfix configuration (in /opt/mailcow-dockerized/data/conf/postfix/extra.cf):
  # Replace HOST_IP with: docker network inspect mailcowdockerized_mailcow-network
  # and use the host gateway address (usually ends in .1)
  smtpd_recipient_restrictions =
      permit_mynetworks,
      permit_sasl_authenticated,
      check_policy_service inet:HOST_IP:10031,
      reject_unauth_destination

  smtpd_sender_restrictions =
      check_policy_service inet:HOST_IP:10031

Firewall: allow port 10031 from the mailcow Docker subnet only.
  ufw allow from 172.22.0.0/16 to any port 10031
"""
import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request

# ── Configuration (all overridable via environment) ───────────────────────────
DJANGO_INTERNAL_URL = os.environ.get("DJANGO_INTERNAL_URL", "http://127.0.0.1:8000").rstrip("/")
INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET", "")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "10031"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "8"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("matemail.policy")

if not INTERNAL_API_SECRET:
    logger.warning(
        "INTERNAL_API_SECRET is not set — Django will reject all policy calls. "
        "Set it in the environment or .env file."
    )


# ── Django HTTP client (synchronous, called in executor) ─────────────────────

def _call_django(path: str, data: dict) -> dict | None:
    """POST JSON to Django. Returns the parsed response dict or None on error."""
    url = f"{DJANGO_INTERNAL_URL}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Secret": INTERNAL_API_SECRET,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode(errors="replace")
        except Exception:
            body = ""
        logger.error("Django %s returned HTTP %s: %s", path, exc.code, body[:200])
        return None
    except Exception as exc:
        logger.error("Django call to %s failed: %s", path, exc)
        return None


# ── Policy decision logic ─────────────────────────────────────────────────────

def _to_postfix_action(django_response: dict | None) -> str:
    """Convert a Django JSON response to a Postfix action string.
    On Django unreachability, fail CLOSED (DEFER) to prevent open-relay accidents.
    """
    if django_response is None:
        return "DEFER Service temporarily unavailable — please try again shortly"

    action = str(django_response.get("action", "REJECT")).upper()
    reason = str(django_response.get("reason", "")).strip()

    if action == "OK":
        return "DUNNO"
    elif action == "REJECT":
        return f"REJECT {reason}" if reason else "REJECT"
    elif action == "DEFER":
        return f"DEFER_IF_PERMIT {reason}" if reason else "DEFER_IF_PERMIT"
    else:
        logger.warning("Unrecognised Django action %r — defaulting to DUNNO", action)
        return "DUNNO"


def _handle_policy_request(attrs: dict) -> str:
    """
    Given a dict of Postfix policy attributes, call the appropriate Django
    endpoint and return the Postfix action string.
    """
    protocol_state = attrs.get("protocol_state", "").upper()
    sasl_username = attrs.get("sasl_username", "").strip()
    sender = attrs.get("sender", "").strip()
    recipient = attrs.get("recipient", "").strip()
    client_address = attrs.get("client_address", "").strip()

    # Only make a policy decision at the RCPT state.
    # For other states (CONNECT, HELO, MAIL) we pass through — checks happen at RCPT.
    if protocol_state and protocol_state not in ("RCPT", "END_OF_MESSAGE"):
        return "DUNNO"

    if sasl_username:
        # Authenticated SMTP submission → outbound policy (rate limits, suspension, sender check)
        response = _call_django("/api/internal/smtp/outbound/", {
            "sender": sender,
            "sasl_username": sasl_username,
            "client_address": client_address,
        })
        action = _to_postfix_action(response)
        logger.info(
            "OUTBOUND sasl=%s sender=%s addr=%s → %s",
            sasl_username, sender, client_address, action,
        )
        return action
    elif recipient:
        # Inbound delivery → check recipient domain/mailbox is active and not suspended
        response = _call_django("/api/internal/smtp/inbound/", {
            "recipient": recipient,
            "sender": sender,
            "client_address": client_address,
        })
        action = _to_postfix_action(response)
        logger.info(
            "INBOUND recipient=%s sender=%s addr=%s → %s",
            recipient, sender, client_address, action,
        )
        return action
    else:
        return "DUNNO"


# ── Async TCP server ──────────────────────────────────────────────────────────

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Handle a single Postfix policy service TCP connection.

    A connection may carry multiple sequential policy requests; we loop until
    the client closes the connection or sends an empty payload.
    """
    peer = writer.get_extra_info("peername", ("?", 0))
    logger.debug("Connection from %s:%s", *peer)

    try:
        while True:
            # Read one policy request: key=value lines terminated by an empty line
            attrs: dict[str, str] = {}
            while True:
                line_bytes = await reader.readline()
                if not line_bytes:
                    # Client closed connection
                    return
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    break  # empty line = end of this request
                if "=" in line:
                    key, _, value = line.partition("=")
                    attrs[key.strip().lower()] = value.strip()

            if not attrs:
                continue

            # Run the blocking Django HTTP call in a thread-pool executor
            loop = asyncio.get_event_loop()
            action = await loop.run_in_executor(None, _handle_policy_request, attrs)

            # Postfix expects: "action=VALUE\n\n" (double newline to end response)
            writer.write(f"action={action}\n\n".encode())
            await writer.drain()

    except asyncio.IncompleteReadError:
        pass  # client disconnected mid-stream
    except ConnectionResetError:
        pass
    except Exception:
        logger.exception("Unexpected error handling client %s:%s", *peer)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    server = await asyncio.start_server(
        handle_client,
        LISTEN_HOST,
        LISTEN_PORT,
        reuse_address=True,
    )
    addr = server.sockets[0].getsockname()
    logger.info("MateMail policy bridge listening on %s:%s", *addr)
    logger.info("Forwarding to Django at %s", DJANGO_INTERNAL_URL)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down.")
