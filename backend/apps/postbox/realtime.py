"""
Realtime mailbox change fan-out for PostBox web clients.

This is deliberately NOT a mail store and never carries message content.
Events contain only mailbox-state coordinates (folder, UIDVALIDITY, UID) plus
an action/kind. Redis Pub/Sub is an ephemeral wake-up bus: the browser always
re-reads authoritative IMAP state after an event.

The stream endpoint reconnects periodically so PostBox session revocation and
expiry are re-checked by the normal authentication layer. Missing Redis must
never make a mail action fail; at worst a browser misses a wake-up and the
manual refresh remains available.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from functools import lru_cache
from typing import Iterator

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "postbox:realtime:"
STREAM_LIFETIME_SECONDS = 300
HEARTBEAT_SECONDS = 25


@lru_cache(maxsize=1)
def _client() -> redis.Redis:
    return redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=30,
        health_check_interval=30,
    )


def channel(mailbox_id) -> str:
    return f"{CHANNEL_PREFIX}{mailbox_id}"


def publish(
    mailbox,
    *,
    kind: str,
    folder: str = "",
    uid_validity: int | None = None,
    uid: int | None = None,
    action: str = "",
    event_id: str | None = None,
) -> str:
    """Publish one content-free mailbox wake-up. Failure is non-fatal."""
    event_id = event_id or str(uuid.uuid4())
    payload: dict[str, object] = {
        "event_id": event_id,
        "kind": kind,
    }
    if folder:
        payload["folder"] = folder
    if uid_validity is not None:
        payload["uid_validity"] = int(uid_validity)
    if uid is not None:
        payload["uid"] = int(uid)
    if action:
        payload["action"] = action

    try:
        _client().publish(
            channel(mailbox.pk),
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
        )
    except (redis.RedisError, OSError) as exc:
        logger.warning(
            "PostBox realtime publish unavailable for mailbox=%s (%s)",
            mailbox.pk,
            type(exc).__name__,
        )
    return event_id


def stream(mailbox_id) -> Iterator[str]:
    """
    Yield SSE frames for one authenticated mailbox.

    A five-minute stream lifetime intentionally forces EventSource to reconnect,
    re-running PostBox session authentication. Heartbeats stay well below the
    nginx read timeout and also let dead clients be noticed promptly.
    """
    pubsub = None
    deadline = time.monotonic() + STREAM_LIFETIME_SECONDS
    try:
        pubsub = _client().pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(channel(mailbox_id))
        yield "retry: 3000\n\n"
        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            timeout = min(float(HEARTBEAT_SECONDS), remaining)
            if timeout <= 0:
                break
            message = pubsub.get_message(timeout=timeout)
            if message and message.get("type") == "message":
                data = message.get("data")
                if isinstance(data, str):
                    yield f"event: mailbox\ndata: {data}\n\n"
            else:
                yield ": keepalive\n\n"
    except (redis.RedisError, OSError) as exc:
        logger.warning(
            "PostBox realtime stream unavailable for mailbox=%s (%s)",
            mailbox_id,
            type(exc).__name__,
        )
    finally:
        if pubsub is not None:
            try:
                pubsub.close()
            except (redis.RedisError, OSError):
                pass
