import time

import redis
import requests
from django.conf import settings
from django.db import connection, OperationalError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


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
        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        client.ping()
        return "ok", round((time.monotonic() - start) * 1000, 2)
    except Exception:
        return "error", -1


def _check_mail_engine() -> tuple[str, float]:
    api_url = getattr(settings, "MAIL_ENGINE_API_URL", "")
    if not api_url:
        return "unknown", -1
    start = time.monotonic()
    try:
        resp = requests.get(
            f"{api_url}/api/v1/info",
            headers={"X-API-Key": settings.MAIL_ENGINE_API_KEY},
            timeout=3,
        )
        if resp.ok:
            return "ok", round((time.monotonic() - start) * 1000, 2)
        return "error", -1
    except Exception:
        return "unknown", -1


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    db_status, _ = _check_db()
    redis_status, _ = _check_redis()
    mail_status, _ = _check_mail_engine()

    overall = (
        "ok"
        if db_status == "ok" and redis_status == "ok"
        else "degraded"
    )
    status_code = 200 if overall == "ok" else 503

    return Response(
        {
            "status": overall,
            "service": "MateMail",
            "version": "0.1.0",
            "checks": {
                "db": db_status,
                "redis": redis_status,
                "mail_engine": mail_status,
            },
        },
        status=status_code,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def health_db(request):
    status, latency_ms = _check_db()
    return Response(
        {"status": status, "latency_ms": latency_ms},
        status=200 if status == "ok" else 503,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def health_redis(request):
    status, latency_ms = _check_redis()
    return Response(
        {"status": status, "latency_ms": latency_ms},
        status=200 if status == "ok" else 503,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def health_mail_engine(request):
    status, latency_ms = _check_mail_engine()
    return Response(
        {"status": status, "latency_ms": latency_ms},
        status=200 if status in ("ok", "unknown") else 503,
    )
