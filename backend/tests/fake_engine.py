"""
In-memory fake of the Mail Engine's REST surface.

This lets `MailcowAdapter` be exercised against the same contract as
`StubAdapter` without a running engine. It replaces the adapter's
`requests.Session`, so every code path in the real adapter runs for real:
payload construction, status handling, response-envelope parsing and error
classification.

It models the engine's quirks that the adapter must cope with:

- Application-level failure is reported inside a **200** response body as
  ``[{"type": "error", "msg": ...}]``, not via the status code.
- ``add`` on an existing object reports "already exists" rather than replacing.
- Quotas are returned in **bytes**, while the API accepts them in MB.

`fail_next` and `fail_all` inject transport and rejection failures so the
error-mapping tests drive the genuine adapter code rather than a mock of it.
"""
from __future__ import annotations

import json

import requests


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else json.dumps(payload if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _ok(payload=None):
    return FakeResponse(200, payload if payload is not None else [{"type": "success", "msg": "ok"}])


def _engine_error(message: str):
    """A 200 carrying an application-level failure, as the real engine does."""
    return FakeResponse(200, [{"type": "error", "msg": message}])


class FakeEngineSession:
    """Stands in for `requests.Session` inside MailcowAdapter."""

    def __init__(self):
        self.headers: dict[str, str] = {}
        self.domains: dict[str, dict] = {}
        self.mailboxes: dict[str, dict] = {}
        self.aliases: dict[str, dict] = {}
        self.dkim: dict[str, dict] = {}
        self.queue: list[dict] = []
        self.quarantine: list[dict] = []
        self._next_alias_id = 1

        #: Raise a transport error on the next call.
        self.fail_next_transport = False
        #: Return this engine error message on the next mutating call.
        self.fail_next_message: str | None = None
        #: Status code to force on the next call.
        self.fail_next_status: int | None = None
        #: Every call fails with a transport error while True.
        self.fail_all_transport = False
        #: Calls made, for idempotency assertions.
        self.calls: list[tuple[str, str]] = []

    # ── Transport ───────────────────────────────────────────────────────────

    def request(self, method, url, json=None, timeout=None):  # noqa: A002
        path = url.split("8080", 1)[-1] if "8080" in url else url
        for prefix in ("http://", "https://"):
            if path.startswith(prefix):
                path = "/" + path.split("/", 3)[-1]
        self.calls.append((method, path))

        if self.fail_all_transport or self.fail_next_transport:
            self.fail_next_transport = False
            raise requests.ConnectionError(
                "HTTPConnectionPool(host='mail-engine-nginx', port=8080): "
                "Max retries exceeded with url: /api/v1/add/mailbox"
            )

        if self.fail_next_status is not None:
            status = self.fail_next_status
            self.fail_next_status = None
            return FakeResponse(status, None, text="mailcow backend failure detail")

        if self.fail_next_message is not None:
            message = self.fail_next_message
            self.fail_next_message = None
            return _engine_error(message)

        return self._route(method, path, json or {})

    # ── Routing ─────────────────────────────────────────────────────────────

    def _route(self, method, path, body):
        if path.startswith("/api/v1/get/"):
            return self._handle_get(path)
        if method == "DELETE":
            return self._handle_delete(path, body)
        if method == "POST":
            return self._handle_post(path, body)
        return FakeResponse(405, None, text="method not allowed")

    def _handle_get(self, path):
        rest = path[len("/api/v1/get/"):].rstrip("/")

        if rest == "domain/all":
            return _ok(list(self.domains.values()))
        if rest == "status/containers":
            return _ok({"engine": "running"})
        if rest == "mailq/all":
            return _ok(self.queue)
        if rest == "quarantine/all":
            return _ok(self.quarantine)

        if rest.startswith("mailbox/all/"):
            domain = rest[len("mailbox/all/"):]
            return _ok([
                row for row in self.mailboxes.values()
                if row["username"].endswith(f"@{domain}")
            ])
        if rest.startswith("mailbox/"):
            scope = rest[len("mailbox/"):]
            if scope == "all":
                return _ok(list(self.mailboxes.values()))
            row = self.mailboxes.get(scope)
            return _ok([row]) if row else FakeResponse(404, None, text="not found")

        if rest.startswith("alias/"):
            address = rest[len("alias/"):]
            row = self.aliases.get(address)
            return _ok([row]) if row else FakeResponse(404, None, text="not found")

        if rest.startswith("dkim/"):
            domain = rest[len("dkim/"):]
            row = self.dkim.get(domain)
            return _ok(row) if row else FakeResponse(404, None, text="not found")

        return FakeResponse(404, None, text="unknown path")

    def _handle_post(self, path, body):
        # ── domains ──
        if path == "/api/v1/add/domain":
            name = body["domain"]
            if name in self.domains:
                return _engine_error(f"domain {name} already exists")
            self.domains[name] = {
                "domain_name": name,
                "active": body.get("active", "1"),
                "mailboxes": body.get("mailboxes"),
                "maxquota": body.get("maxquota"),
            }
            return _ok()

        if path == "/api/v1/edit/domain":
            missing = [d for d in body["items"] if d not in self.domains]
            if missing:
                return _engine_error(f"domain {missing[0]} not found")
            for name in body["items"]:
                self.domains[name].update(
                    {k: v for k, v in body.get("attr", {}).items()}
                )
            return _ok()

        # ── mailboxes ──
        if path == "/api/v1/add/mailbox":
            address = f"{body['local_part']}@{body['domain']}"
            if address in self.mailboxes:
                return _engine_error(f"mailbox {address} already exists")
            quota_mb = int(body.get("quota", 0) or 0)
            self.mailboxes[address] = {
                "username": address,
                "name": body.get("name", ""),
                "active": body.get("active", "1"),
                "quota": quota_mb * 1024 * 1024,
                "quota_used": 0,
                "messages": 0,
                "last_imap_login": "0",
                "_has_password": bool(body.get("password")),
            }
            return _ok()

        if path == "/api/v1/edit/mailbox":
            missing = [a for a in body["items"] if a not in self.mailboxes]
            if missing:
                return _engine_error(f"mailbox {missing[0]} not found")
            for address in body["items"]:
                attr = body.get("attr", {})
                row = self.mailboxes[address]
                if "quota" in attr:
                    row["quota"] = int(attr["quota"]) * 1024 * 1024
                if "name" in attr:
                    row["name"] = attr["name"]
                if "active" in attr:
                    row["active"] = attr["active"]
                if attr.get("password"):
                    row["_has_password"] = True
            return _ok()

        # ── aliases ──
        if path == "/api/v1/add/alias":
            address = body["address"]
            if address in self.aliases:
                return _engine_error(f"alias {address} already exists")
            self.aliases[address] = {
                "id": self._next_alias_id,
                "address": address,
                "goto": body["goto"],
                "active": body.get("active", "1"),
            }
            self._next_alias_id += 1
            return _ok()

        if path == "/api/v1/edit/alias":
            ids = set(body["items"])
            for row in self.aliases.values():
                if row["id"] in ids:
                    row.update({k: v for k, v in body.get("attr", {}).items()})
            return _ok()

        # ── dkim ──
        if path == "/api/v1/add/dkim":
            domain = body["domains"]
            selector = body.get("dkim_selector", "mm1")
            # Each rotation yields a new public key; the private key never
            # leaves the engine, so the fake does not even model one.
            serial = len(self.dkim.get(domain, {}).get("_history", [])) + 1
            public = f"FAKEPUB{serial:03d}"
            self.dkim[domain] = {
                "dkim_selector": selector,
                "pubkey": public,
                "dkim_txt": f"v=DKIM1;k=rsa;p={public}",
                "_history": [public] * serial,
            }
            return _ok()

        # ── queue / quarantine ──
        if path == "/api/v1/delete/mailq":
            ids = set(body.get("id", []))
            self.queue = [m for m in self.queue if m.get("queue_id") not in ids]
            return _ok()

        if path == "/api/v1/set/quarantine/release":
            item = body.get("item")
            self.quarantine = [q for q in self.quarantine if q.get("id") != item]
            return _ok()

        return FakeResponse(404, None, text="unknown path")

    def _handle_delete(self, path, body):
        if path == "/api/v1/delete/domain":
            names = body if isinstance(body, list) else [body]
            unknown = [n for n in names if n not in self.domains]
            if unknown:
                return _engine_error(f"domain {unknown[0]} not found")
            for name in names:
                self.domains.pop(name, None)
                for address in [a for a in self.mailboxes if a.endswith(f"@{name}")]:
                    self.mailboxes.pop(address, None)
                self.dkim.pop(name, None)
            return _ok()

        if path == "/api/v1/delete/mailbox":
            addresses = body if isinstance(body, list) else [body]
            unknown = [a for a in addresses if a not in self.mailboxes]
            if unknown:
                return _engine_error(f"mailbox {unknown[0]} does not exist")
            for address in addresses:
                self.mailboxes.pop(address, None)
            return _ok()

        if path == "/api/v1/delete/alias":
            ids = set(body if isinstance(body, list) else [body])
            for address, row in list(self.aliases.items()):
                if row["id"] in ids:
                    self.aliases.pop(address, None)
            return _ok()

        return FakeResponse(404, None, text="unknown path")


def build_mailcow_adapter():
    """A real MailcowAdapter wired to a fake engine. Returns (adapter, session)."""
    from apps.mail_engine.mailcow_adapter import MailcowAdapter

    adapter = MailcowAdapter()
    session = FakeEngineSession()
    adapter._session = session
    return adapter, session
