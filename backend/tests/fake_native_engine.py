"""
In-process transport for `NativeMailEngineAdapter`.

WHAT MAKES THIS DIFFERENT FROM `fake_engine.py`
    The mailcow fake has to REIMPLEMENT the engine, because the real one is a
    third-party PHP application nobody can import. Every behaviour it models is
    a second implementation that can drift from the thing it stands for, which
    is why P4C-A found both test doubles agreeing with the adapter's assumption
    instead of the engine's behaviour.

    The Native Engine is ours, so nothing needs reimplementing. This dispatches
    straight into `app._READ_ROUTES` and `app._WRITE_ROUTES` — the same tables
    the HTTP server dispatches through — against a real PostgreSQL database
    running the real migrations. What gets skipped is the socket, not the logic.

    So a contract test here exercises the actual validation, the actual SQL, the
    actual DKIM key generation and the actual response guard.
"""
from __future__ import annotations

import json
import sys
import urllib.parse


class FakeResponse:
    """The subset of `requests.Response` the adapter uses."""

    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class NativeApiSession:
    """
    Stands in for `requests.Session` inside NativeMailEngineAdapter.

    Mirrors `app.do_GET` / `app.do_POST` exactly, including their status-code
    mapping, so the adapter's error classification is exercised for real.
    """

    def __init__(self, conn, app_module, provisioning_module, validation_module, db_module):
        self.conn = conn
        self.app = app_module
        self.provisioning = provisioning_module
        self.validation = validation_module
        self.db = db_module
        self.headers = {}

    def request(self, method, url, json=None, params=None, timeout=None, **kwargs):
        path = urllib.parse.urlparse(url).path.rstrip("/") or "/"
        query = {k: str(v) for k, v in (params or {}).items()}

        if path == "/ready":
            version = self.db.current_version(self.conn)
            ready = version >= self.db.REQUIRED_VERSION
            return FakeResponse(200 if ready else 503, {
                "ready": ready,
                "schema_version": version,
                "required_schema_version": self.db.REQUIRED_VERSION,
            })

        table = self.app._READ_ROUTES if method == "GET" else self.app._WRITE_ROUTES
        handler = table.get(path)
        if handler is None:
            return FakeResponse(404, {"error": "not found"})

        try:
            result = handler(self.conn, query if method == "GET" else (json or {}))
            if result is None:
                return FakeResponse(404, {"error": "not found"})
            # The same guard the server applies before writing a response, so a
            # handler that leaked private material would fail here too.
            self.app._assert_response_is_safe(result)
            return FakeResponse(200, result)
        except self.validation.ValidationError as exc:
            return FakeResponse(400, {"error": exc.message, "field": exc.field})
        except self.provisioning.NotFound as exc:
            return FakeResponse(404, {"error": str(exc)})
        except Exception as exc:                       # noqa: BLE001
            # Matches `Handler._fail`: the type, never the message.
            return FakeResponse(500, {"error": "internal error",
                                      "detail": type(exc).__name__})


def build_native_adapter(conn, engine_dir):
    """
    A real NativeMailEngineAdapter wired to the real handlers.

    Returns (adapter, session). The caller owns `conn` and the scratch database.
    """
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))

    import app as app_module
    import db as db_module
    import provisioning as provisioning_module
    import validation as validation_module

    from apps.mail_engine.native_adapter import NativeMailEngineAdapter

    adapter = NativeMailEngineAdapter(
        base_url="http://matemail-native-api.invalid:8451",
        secret="test-only-not-a-real-secret",
    )
    session = NativeApiSession(
        conn, app_module, provisioning_module, validation_module, db_module
    )
    adapter._session = session
    return adapter, session
