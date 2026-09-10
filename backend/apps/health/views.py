"""
Health endpoints.

Two audiences, deliberately separated:

**Public** (`/api/health/`) — for load balancers and container healthchecks. It
reports whether MateMail can serve requests and nothing else. It must not reveal
which components exist, that a distinct Mail Engine exists, or how any of them
are doing: that is infrastructure detail, and an unauthenticated caller has no
business learning it (DEC-011).

**Operator** (`/api/internal/health/`) — full component detail, behind
INTERNAL_API_SECRET and denied at the edge by nginx along with the rest of
`/api/internal/`.
"""
import hmac
import logging
import time

import redis
from django.conf import settings
from django.db import OperationalError, connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def _authorized(request) -> bool:
    """Constant-time check of the internal shared secret. Fails closed."""
    expected = getattr(settings, "INTERNAL_API_SECRET", "")
    if not expected:
        logger.error("INTERNAL_API_SECRET not configured — rejecting internal call")
        return False
    provided = request.META.get("HTTP_X_INTERNAL_SECRET", "")
    return hmac.compare_digest(provided.encode(), expected.encode())


def _check_db() -> tuple[str, float]:
    start = time.monotonic()
    try:
        connection.ensure_connection()
        return "ok", round((time.monotonic() - start) * 1000, 2)
    except OperationalError:
        return "error", -1


def _check_redis() -> tuple[str, float]:
    start = time.monotonic()
    try:
        redis.from_url(settings.REDIS_URL, socket_connect_timeout=2).ping()
        return "ok", round((time.monotonic() - start) * 1000, 2)
    except Exception:
        # Health checks report; they never raise into the caller.
        return "error", -1


def _check_mail_engine() -> tuple[str, float]:
    from apps.mail_engine.factory import get_adapter

    start = time.monotonic()
    health = get_adapter().check_health()
    if health.reachable:
        return "ok", round((time.monotonic() - start) * 1000, 2)
    logger.warning("Mail Engine health check failed: %s", health.detail)
    return "error", -1


# ── Public ───────────────────────────────────────────────────────────────────


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """
    Public liveness probe.

    Deliberately minimal: `status` plus the service name. Whether the datastores
    or the Mail Engine are healthy is decided here to set the status code, but
    the per-component breakdown is never returned. The Mail Engine is excluded
    from the verdict entirely, so mail-side trouble cannot take the web tier out
    of a load balancer.
    """
    db_status, _ = _check_db()
    redis_status, _ = _check_redis()
    healthy = db_status == "ok" and redis_status == "ok"
    return Response(
        {"status": "ok" if healthy else "unavailable", "service": "MateMail"},
        status=200 if healthy else 503,
    )


# ── Operator-only ────────────────────────────────────────────────────────────


@api_view(["GET"])
@permission_classes([AllowAny])
def health_internal(request):
    """
    GET /api/internal/health/ — full component detail for operators.

    Requires X-Internal-Secret. Mounted under /api/internal/ so the edge deny
    rule covers it.
    """
    if not _authorized(request):
        return Response({"detail": "Unauthorized"}, status=403)

    db_status, db_ms = _check_db()
    redis_status, redis_ms = _check_redis()
    engine_status, engine_ms = _check_mail_engine()

    core_ok = db_status == "ok" and redis_status == "ok"
    return Response(
        {
            "status": "ok" if core_ok and engine_status == "ok" else "degraded",
            "service": "MateMail",
            "version": "0.1.0",
            "checks": {
                "database": {"status": db_status, "latency_ms": db_ms},
                "cache": {"status": redis_status, "latency_ms": redis_ms},
                "mail_engine": {"status": engine_status, "latency_ms": engine_ms},
            },
        },
        status=200 if core_ok else 503,
    )
