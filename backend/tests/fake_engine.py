"""
In-memory fake of the Mail Engine's REST surface.

This lets `MailcowAdapter` be exercised against the same contract as
`StubAdapter` without a running engine. It replaces the adapter's
`requests.Session`, so every code path in the real adapter runs for real:
payload construction, status handling, response-envelope parsing and error
classification.

It models the engine's quirks that the adapter must cope with:

- Application-level failure is reported inside a **200** response body as
  ``[{"type": "error", "msg": ...}]``, not via the status code, and ``msg`` is
  sometimes a list rather than a string.
- ``add`` on an existing object reports "already exists" rather than replacing.
- Quotas are returned in **bytes**, while the API accepts them in MB.

It also models the DKIM lifecycle measured against the live engine in P4B.
These four behaviours belong together and are the reason this file was wrong
before:

- ``add/domain`` generates the DKIM keypair, using the selector and key size
  sent on that call.
- ``add/dkim`` **refuses** a domain that already has a key.
- ``delete/domain`` does **not** remove the key.
- ``delete/dkim`` is the only thing that does.

A fake that quietly cleaned up after ``delete/domain``, or let ``add/dkim``
overwrite, is a fake that agrees with the bugs instead of catching them — which
is what happened. See ``docs/MAIL_ENGINE.md`` § "DKIM lifecycle".

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


def _engine_error_list(message: list):
    """
    The same envelope with a *list* `msg`.

    The engine returns `msg` as an array for its translated messages — e.g.
    `["dkim_domain_or_sel_invalid", "example.com"]` — and as a plain string
    elsewhere. The adapter has to cope with both, so the fake produces both.
    """
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
        self._dkim_serial = 0
        #: address -> {"value": str, "frame": str}. The engine stores a cleared
        #: limit by deleting the key, so 0 removes rather than stores.
        self.rate_limits: dict[str, dict] = {}

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

    # ── Quotas ──────────────────────────────────────────────────────────────

    @staticmethod
    def _reject_invalid_quotas(body):
        """
        The engine's storage invariant: ``defquota <= maxquota <= quota``.

        This fake used to accept any combination, which is exactly why the
        adapter shipped sending ``quota: 0`` alongside a positive per-mailbox
        ceiling. Every request-contract test passed; the first real domain
        creation against the live engine failed with
        ``mailbox_quota_exceeds_domain_quota``, and every mailbox call after it
        failed with ``access_denied`` because the domain did not exist.

        A test double that accepts what the real thing refuses is worse than no
        double at all — it converts a loud production failure into a green
        suite. The messages below are the engine's own.

        Returns a rejection response, or ``None`` when the values are valid.
        """
        values = {}
        for key in ("defquota", "maxquota", "quota"):
            raw = body.get(key)
            if raw is None:
                continue
            try:
                values[key] = int(raw)
            except (TypeError, ValueError):
                return _engine_error(f"{key}_invalid")
            if values[key] < 0:
                return _engine_error(f"{key}_invalid")

        defquota = values.get("defquota")
        maxquota = values.get("maxquota")
        quota = values.get("quota")

        # The real engine reads quota=0 as a hard total of zero, NOT unlimited.
        # Any positive per-mailbox ceiling then exceeds it.
        if quota is not None and maxquota is not None and maxquota > quota:
            return _engine_error("mailbox_quota_exceeds_domain_quota")
        if maxquota is not None and defquota is not None and defquota > maxquota:
            return _engine_error("mailbox_defquota_exceeds_mailbox_maxquota")
        return None

    # ── DKIM ────────────────────────────────────────────────────────────────

    def _mint_dkim(self, domain: str, selector=None, key_size=None) -> None:
        """
        Generate public DKIM material for a domain.

        The private key never leaves the engine, so the fake does not model one
        at all — there is nothing here that a leak test could find, which is the
        point. `privkey` is still present and empty, because the real engine
        returns that field on every read (empty while SHOW_DKIM_PRIV_KEYS is
        false) and the adapter must strip it regardless.

        An unset selector falls back to the engine's own default of "dkim", NOT
        to MateMail's "mm1". That difference is the whole reason ensure_domain
        has to send the selector explicitly.
        """
        self._dkim_serial += 1
        public = f"FAKEPUB{self._dkim_serial:03d}"
        self.dkim[domain] = {
            "dkim_selector": selector or "dkim",
            "pubkey": public,
            "dkim_txt": f"v=DKIM1;k=rsa;p={public}",
            "length": str(key_size or 2048),
            "privkey": "",
        }

    # ── Routing ─────────────────────────────────────────────────────────────

    def _route(self, method, path, body):
        if path.startswith("/api/v1/get/"):
            return self._handle_get(path)
        # The real engine routes deletes by PATH, not by HTTP verb, and its
        # API refuses anything but POST with 405 ("only POST method is
        # allowed" — json_api.php @ 2026-07b). This fake previously accepted
        # DELETE, which is how the adapter's wrong verb went unnoticed: the
        # fake was modelling a contract the engine does not offer.
        if method != "POST":
            return FakeResponse(405, None, text="only POST method is allowed")
        if path.startswith("/api/v1/delete/"):
            return self._handle_delete(path, body)
        return self._handle_post(path, body)

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

        if rest.startswith("rl-mbox/"):
            address = rest[len("rl-mbox/"):]
            row = self.rate_limits.get(address)
            # The engine answers with an EMPTY SHAPE, not 404, when no limit is
            # set. A fake that 404'd here would let an adapter treat "no limit"
            # and "no mailbox" as the same thing.
            return _ok(row if row else {})

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
            invalid = self._reject_invalid_quotas(body)
            if invalid is not None:
                return invalid
            self.domains[name] = {
                "domain_name": name,
                "active": body.get("active", "1"),
                "mailboxes": body.get("mailboxes"),
                "maxquota": body.get("maxquota"),
            }
            # Creating a domain also mints its DKIM keypair. The engine does
            # this from inside its own domain-add handler, passing through the
            # dkim_selector and key_size sent on THIS call — which is why those
            # fields have to be here and cannot be supplied later.
            #
            # Only when the domain has no key yet: a domain re-added after a
            # delete that did not clear DKIM keeps the surviving key. That is
            # the cross-tenant hazard, modelled deliberately (see delete/domain).
            if name not in self.dkim:
                self._mint_dkim(name, body.get("dkim_selector"), body.get("key_size"))
            return _ok()

        if path == "/api/v1/edit/domain":
            missing = [d for d in body["items"] if d not in self.domains]
            if missing:
                # The engine's actual message, measured: "domain_invalid", NOT
                # anything containing "not found". That matters, because the
                # adapter classifies by message text — so this rejection becomes
                # Rejected, not NotFound, and a caller that catches only
                # NotFound for an absent domain does not catch it.
                #
                # The invented "domain ... not found" this used to return made
                # exactly that mistake look correct in tests.
                return _engine_error("domain_invalid")
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
            # The engine REFUSES to generate a key for a domain that already has
            # one (functions.dkim.inc.php:21 @ 2026-07b — it checks Redis for an
            # existing public key and bails before validating anything else).
            # This fake used to overwrite happily, which is precisely why the
            # adapter's "rotation = add/dkim" bug survived review: against this
            # double it worked, against the real engine it could never work.
            # The message shape is the engine's own: a list, not a string.
            if domain in self.dkim:
                return _engine_error_list(["dkim_domain_or_sel_invalid", domain])
            self._mint_dkim(domain, body.get("dkim_selector"), body.get("key_size"))
            return _ok()

        # ── rate limits ──
        if path == "/api/v1/edit/rl-mbox":
            attr = body.get("attr", {}) or {}
            frame = str(attr.get("rl_frame", ""))
            # The engine accepts exactly these four frames and rejects anything
            # else with `rl_timeframe` (functions.ratelimit.inc.php @ 2026-07b).
            if frame not in ("s", "m", "h", "d"):
                return _engine_error("rl_timeframe")
            try:
                value = int(attr.get("rl_value", 0))
            except (TypeError, ValueError):
                return _engine_error("rl_value_invalid")
            for address in body.get("items", []):
                if value <= 0:
                    # Zero clears. Modelled because the adapter's
                    # clear_mailbox_rate_limit relies on exactly this.
                    self.rate_limits.pop(address, None)
                else:
                    self.rate_limits[address] = {"value": str(value), "frame": frame}
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
                # The DKIM key is deliberately NOT removed. Deleting a domain
                # leaves its keypair behind in the real engine — measured
                # directly in P4B, where a domain was deleted and its key was
                # still readable afterwards, and re-adding the same domain
                # adopted it. That is how one tenant can end up holding the
                # signing key for a domain a different tenant now owns.
                #
                # This fake used to pop it here, which made the deprovisioning
                # path look correct in tests while production leaked the key.
                # Removing a key requires an explicit delete/dkim, below.
            return _ok()

        if path == "/api/v1/delete/dkim":
            names = body if isinstance(body, list) else [body]
            for name in names:
                self.dkim.pop(name, None)
            # Idempotent: deleting a key that is not there is a success, so no
            # not-found branch. Matches the engine, which reports success for a
            # domain it holds no key for.
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
