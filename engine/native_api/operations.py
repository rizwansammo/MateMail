"""
Native Engine operational surface (NE4).

WHAT THIS COVERS
    The four things NE4 added that are not provisioning state: real mailbox
    usage, the queue, quarantine, and the rate limits the mail path enforces.

WHY IT IS A SEPARATE MODULE
    `provisioning.py` owns engine STATE — the rows that describe what exists.
    These are operations ON a running engine: they ask Dovecot how full a
    mailbox is, ask Postfix what is queued, and tell Postfix to drop or release
    one message. Mixing them would blur the rule that every write to engine
    state lives in one reviewed file.

    The rate-limit functions are the exception and deliberately live in
    `provisioning.py` instead: a limit is stored configuration, not an action.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request

logger = logging.getLogger("matemail.native.api.operations")


class OperationUnavailable(RuntimeError):
    """A component the operation depends on could not be reached."""


class InvalidIdentifier(ValueError):
    """A caller-supplied identifier is not well formed."""


# Postfix queue ids in both the classic hexadecimal and the long form. This is
# validated HERE as well as in the control plane: the control plane's check is
# what protects the command, and this one keeps a malformed id from travelling
# any further than it has to.
_QUEUE_ID = re.compile(r"^[A-Za-z0-9]{6,32}$")

CONTROL_URL = os.environ.get("NATIVE_CONTROL_URL", "http://postfix:8460")
CONTROL_SECRET = os.environ.get("NATIVE_CONTROL_SECRET", "")

DOVEADM_URL = os.environ.get("NATIVE_DOVEADM_URL", "http://dovecot:8080")
DOVEADM_API_KEY = os.environ.get("NATIVE_DOVEADM_API_KEY", "")

_TIMEOUT = 20


def _require_queue_id(value: str) -> str:
    if not isinstance(value, str) or not _QUEUE_ID.match(value or ""):
        raise InvalidIdentifier("queue id is not well formed")
    return value


# ── Postfix control plane ───────────────────────────────────────────────────


def _control(method: str, path: str, payload: dict | None = None) -> dict:
    if not CONTROL_SECRET:
        raise OperationUnavailable("the engine control secret is not configured")

    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(CONTROL_URL + path, data=data, method=method)
    request.add_header("X-Engine-Control-Secret", CONTROL_SECRET)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:200]
        raise OperationUnavailable(
            f"control plane returned {exc.code}: {body}"
        ) from exc
    except Exception as exc:                     # noqa: BLE001
        raise OperationUnavailable(
            f"control plane unreachable: {type(exc).__name__}"
        ) from exc


def queue_status() -> list[dict]:
    """Everything queued and not held."""
    return _control("GET", "/queue").get("items", [])


def quarantine_items() -> list[dict]:
    """Everything in the hold queue."""
    return _control("GET", "/quarantine").get("items", [])


def cancel_queue_message(queue_id: str) -> dict:
    return _control("POST", "/queue/cancel", {"queue_id": _require_queue_id(queue_id)})


def release_quarantine_item(queue_id: str) -> dict:
    return _control(
        "POST", "/quarantine/release", {"queue_id": _require_queue_id(queue_id)}
    )


# ── Dovecot administrative API ──────────────────────────────────────────────


def _doveadm(command: str, parameters: dict) -> list:
    """
    One doveadm command over Dovecot's HTTP API.

    The API takes a list of commands, each `[name, parameters, tag]`, and
    answers with a list of `[type, payload, tag]`. Only read commands are used
    from here — this is a reporting path, and nothing in NE4 needs doveadm to
    change a mailbox.
    """
    if not DOVEADM_API_KEY:
        raise OperationUnavailable("the doveadm API key is not configured")

    body = json.dumps([[command, parameters, "ne4"]]).encode()
    request = urllib.request.Request(DOVEADM_URL + "/doveadm/v1", data=body, method="POST")
    # Dovecot's own scheme: the shared key, base64 encoded, under Authorization.
    token = base64.b64encode(DOVEADM_API_KEY.encode()).decode()
    request.add_header("Authorization", f"X-Dovecot-API {token}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read() or b"[]")
    except urllib.error.HTTPError as exc:
        raise OperationUnavailable(f"doveadm returned {exc.code}") from exc
    except Exception as exc:                     # noqa: BLE001
        raise OperationUnavailable(
            f"doveadm unreachable: {type(exc).__name__}"
        ) from exc


def mailbox_usage(address: str) -> dict | None:
    """
    Real storage consumption for one mailbox, from Dovecot.

    NOT derived from the quota column, which records what the mailbox is
    ALLOWED, and not measured by walking the Maildir, which would make Django
    read customer mail to produce a number.

    UNITS. doveadm reports storage in KILOBYTES and messages as a count. The
    adapter contract is in MEGABYTES. The conversion happens once, here, and the
    value is rounded DOWN so a mailbox is never reported as fuller than it is.
    """
    try:
        replies = _doveadm("quotaGet", {"user": address})
    except OperationUnavailable:
        raise

    if not replies or not isinstance(replies, list):
        return None
    reply = replies[0]
    if not isinstance(reply, list) or len(reply) < 2 or reply[0] != "doveadmResponse":
        # `error` is what Dovecot returns for an unknown user, which is a
        # legitimate answer to "how full is this mailbox" — it does not exist.
        return None

    used_kb = None
    quota_kb = None
    message_count = None
    for row in reply[1] or []:
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        if kind == "STORAGE":
            used_kb = _as_int(row.get("value"))
            quota_kb = _as_int(row.get("limit"))
        elif kind == "MESSAGE":
            message_count = _as_int(row.get("value"))

    if used_kb is None:
        return None
    return {
        "address": address,
        "used_mb": used_kb // 1024,
        "quota_mb": (quota_kb // 1024) if quota_kb else 0,
        "message_count": message_count,
    }


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
